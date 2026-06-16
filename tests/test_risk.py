import pytest

from bot.risk import RiskConfig, RiskManager


def test_kill_switch_trips_at_daily_loss_limit():
    rm = RiskManager(RiskConfig(max_daily_loss_pct=0.03), day_start_equity=100_000)
    assert not rm.update(98_000)  # -2%, still trading
    assert rm.update(96_000)  # -4%, breached
    assert rm.halted
    # Once halted, entries are blocked even if equity recovers.
    decision = rm.evaluate_entry(equity=100_000, price=100, current_exposure_value=0)
    assert not decision.approved
    assert "kill switch" in decision.reason


def test_kill_switch_resets_next_day():
    rm = RiskManager(RiskConfig(max_daily_loss_pct=0.03), 100_000)
    rm.update(90_000)
    assert rm.halted
    rm.reset_day(90_000)
    assert not rm.halted


def test_position_size_respects_cap():
    rm = RiskManager(RiskConfig(max_position_pct=0.10, risk_per_trade_pct=0.01), 100_000)
    # Risk-based sizing (stop at 95 -> $5/share risk, $1000 at risk -> 200 shares)
    # is capped to 10% of equity / $100 = 100 shares.
    assert rm.position_size(equity=100_000, price=100, stop_price=95) == 100


def test_position_size_fractional_flag():
    rm = RiskManager(RiskConfig(max_position_pct=0.10, allow_fractional=True), 100_000)
    qty = rm.position_size(equity=100_000, price=300)
    assert qty == pytest.approx(100_000 * 0.10 / 300)


def test_exposure_gate_blocks_overallocation():
    rm = RiskManager(RiskConfig(max_total_exposure_pct=0.60), 100_000)
    decision = rm.evaluate_entry(equity=100_000, price=100, current_exposure_value=60_000)
    assert not decision.approved
    assert "exposure" in decision.reason


def test_entry_approved_when_within_limits():
    rm = RiskManager(RiskConfig(), 100_000)
    decision = rm.evaluate_entry(equity=100_000, price=100, current_exposure_value=0)
    assert decision.approved
    assert decision.qty > 0


def test_catastrophic_stop_triggers_below_threshold():
    rm = RiskManager(RiskConfig(catastrophic_stop_pct=0.10), 100_000)
    assert rm.should_stop_out(entry_price=100, current_price=89)  # -11% -> stop
    assert rm.should_stop_out(entry_price=100, current_price=90)  # exactly -10% -> stop
    assert not rm.should_stop_out(entry_price=100, current_price=95)  # -5% -> hold
    assert not rm.should_stop_out(entry_price=100, current_price=120)  # winner
    assert not rm.should_stop_out(entry_price=0, current_price=50)  # no entry price -> no stop
