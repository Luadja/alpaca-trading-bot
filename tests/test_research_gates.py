from datetime import date

import pytest

from backtests.universe import ETF_UNIVERSE, load_universe_csv, resolve_universe
from scripts.go_live_check import evaluate_gates


def test_resolve_universe_etf_is_unbiased():
    symbols, windows, biased = resolve_universe("etf")
    assert symbols == ETF_UNIVERSE and windows is None and biased is False


def test_resolve_universe_megacap_is_biased():
    symbols, windows, biased = resolve_universe("megacap")
    assert "AAPL" in symbols and biased is True


def test_resolve_universe_bad_spec_raises():
    with pytest.raises(ValueError):
        resolve_universe("not_a_universe_or_file")


def test_universe_csv_point_in_time(tmp_path):
    p = tmp_path / "u.csv"
    p.write_text("symbol,start,end\nAAPL,2018-01-01,2024-01-01\nFOO,2020-06-01,\n")
    symbols, windows = load_universe_csv(str(p))
    assert symbols == ["AAPL", "FOO"]
    assert windows["AAPL"] == (date(2018, 1, 1), date(2024, 1, 1))
    assert windows["FOO"][0] == date(2020, 6, 1) and windows["FOO"][1] == date.max
    syms, wins, biased = resolve_universe(str(p))
    assert syms == ["AAPL", "FOO"] and wins is not None and biased is False


def _gate(gates, name):
    return next(g for g in gates if g[0] == name)


def test_go_live_gates_all_pass():
    gates = evaluate_gates(
        strategy="trend_momentum", filled_orders=25, min_trades=20,
        halted=False, heartbeat_age=30.0, alerting_enabled=True,
    )
    assert all(passed for _, passed, _, _ in gates)


def test_go_live_gates_fail_conditions():
    gates = evaluate_gates(
        strategy="stoch_rsi_mfi",  # retired -> fail
        filled_orders=3, min_trades=20,  # too few -> fail
        halted=True,  # kill switch latched -> fail
        heartbeat_age=None,  # never ran -> fail
        alerting_enabled=False,  # warn (not required)
    )
    assert _gate(gates, "strategy is validated")[1] is False
    assert _gate(gates, "paper track record")[1] is False
    assert _gate(gates, "kill switch not latched")[1] is False
    assert _gate(gates, "bot has actually run")[1] is False
    # alerting is a WARN, not a required gate
    assert _gate(gates, "alerting configured")[2] is False
