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
    rm.update(96_000)  # -4%: daily breach only (under weekly 6% / monthly 10%)
    assert rm.halted
    rm.reset_day(96_000)
    assert not rm.halted  # daily anchor re-based; weekly/monthly not breached


def test_weekly_latch_survives_recovery_and_day_reset():
    # The regression: a weekly halt must persist even after equity RECOVERS above the
    # weekly threshold and the day rolls — until the WEEK itself rolls.
    rm = RiskManager(RiskConfig(max_daily_loss_pct=0.03, max_weekly_loss_pct=0.06), 100_000)
    assert rm.update(93_000)  # -7%: latches daily AND weekly
    rm.update(98_000)  # recovered to -2% (no longer breaching) — latches must NOT auto-clear
    assert rm.halted
    rm.reset_day(98_000)  # new session: daily latch clears...
    assert rm.halted  # ...but the weekly latch must persist
    rm.reset_week(98_000)  # new week finally clears it
    assert not rm.halted


def test_monthly_limit():
    cfg = RiskConfig(max_daily_loss_pct=0.50, max_weekly_loss_pct=0.50, max_monthly_loss_pct=0.10)
    rm = RiskManager(cfg, 100_000)
    assert not rm.update(95_000)  # -5%: under all
    assert rm.update(89_000)  # -11%: over the 10% monthly limit


def test_snapshot_roundtrip_preserves_halt():
    rm = RiskManager(RiskConfig(), 100_000)
    rm.update(96_000)  # -4%: latches the daily horizon
    snap = rm.snapshot()
    assert snap["halted_day"] is True
    rm2 = RiskManager(
        RiskConfig(),
        snap["day_start_equity"],
        week_start_equity=snap["week_start_equity"],
        month_start_equity=snap["month_start_equity"],
        high_water_mark=snap["high_water_mark"],
        halted_day=snap["halted_day"],
        halted_week=snap["halted_week"],
        halted_month=snap["halted_month"],
    )
    assert rm2.halted  # a tripped kill switch survives reconstruction (restart)
    assert rm2.snapshot() == snap


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


def test_vol_targeting_sizes_inversely_to_volatility():
    rm = RiskManager(
        RiskConfig(use_vol_targeting=True, vol_target_pct=0.02, max_position_pct=0.10), 100_000
    )
    # qty = (equity * 0.02) / (price * sigma) = 2000 / (100 * 0.25) = 80 shares (8% < cap)
    assert rm.position_size(equity=100_000, price=100, sigma=0.25) == 80
    # low-vol name would size huge -> clamped to the 10% position cap (100 shares)
    assert rm.position_size(equity=100_000, price=100, sigma=0.05) == 100
    # higher vol -> smaller position
    assert rm.position_size(equity=100_000, price=100, sigma=0.50) == 40


def test_vol_targeting_off_ignores_sigma():
    rm = RiskManager(RiskConfig(use_vol_targeting=False, max_position_pct=0.10), 100_000)
    # falls back to the position cap when no stop given; sigma is ignored
    assert rm.position_size(equity=100_000, price=100, sigma=0.25) == 100


def test_halt_reason_names_the_breached_horizon():
    # The kill-switch log/alert must report the horizon that ACTUALLY tripped, not always
    # "daily". A weekly-only breach should name week (and its %), not day.
    rm = RiskManager(
        RiskConfig(max_daily_loss_pct=0.50, max_weekly_loss_pct=0.06, max_monthly_loss_pct=0.50),
        day_start_equity=100_000,
    )
    rm.update(93_000)  # -7% vs week start: weekly latch only (daily/monthly thresholds are 50%)
    assert rm.halted
    reason = rm.halt_reason(93_000)
    assert "week" in reason and "-7.00%" in reason
    assert "day" not in reason and "month" not in reason


def test_halt_reason_reports_all_latched_horizons():
    rm = RiskManager(
        RiskConfig(max_daily_loss_pct=0.03, max_weekly_loss_pct=0.06, max_monthly_loss_pct=0.10),
        day_start_equity=100_000,
    )
    rm.update(88_000)  # -12%: trips day, week AND month at once
    reason = rm.halt_reason(88_000)
    assert "day" in reason and "week" in reason and "month" in reason


def test_per_horizon_pnl_helpers():
    rm = RiskManager(RiskConfig(), day_start_equity=100_000)
    rm.reset_week(50_000)
    rm.reset_month(200_000)
    assert rm.daily_pnl_pct(90_000) == pytest.approx(-0.10)
    assert rm.weekly_pnl_pct(45_000) == pytest.approx(-0.10)
    assert rm.monthly_pnl_pct(180_000) == pytest.approx(-0.10)


def test_catastrophic_stop_triggers_below_threshold():
    rm = RiskManager(RiskConfig(catastrophic_stop_pct=0.10), 100_000)
    assert rm.should_stop_out(entry_price=100, current_price=89)  # -11% -> stop
    assert rm.should_stop_out(entry_price=100, current_price=90)  # exactly -10% -> stop
    assert not rm.should_stop_out(entry_price=100, current_price=95)  # -5% -> hold
    assert not rm.should_stop_out(entry_price=100, current_price=120)  # winner
    assert not rm.should_stop_out(entry_price=0, current_price=50)  # no entry price -> no stop


def test_trailing_drawdown_breaker_latches_and_clears():
    # Isolate the peak-to-trough breaker by setting the calendar loss limits high.
    rm = RiskManager(
        RiskConfig(max_drawdown_from_peak_pct=0.20, max_daily_loss_pct=0.99,
                   max_weekly_loss_pct=0.99, max_monthly_loss_pct=0.99),
        day_start_equity=100_000,
    )
    assert not rm.update(110_000)  # new high-water mark
    assert not rm.update(95_000)   # -13.6% from peak, under the 20% breaker
    assert rm.update(85_000)       # -22.7% from peak -> latched
    assert rm.halted and "drawdown" in rm.halt_reason(85_000)
    assert not rm.update(120_000)  # a new high clears the drawdown latch
    assert not rm.halted


def test_trailing_drawdown_off_when_zero():
    rm = RiskManager(
        RiskConfig(max_daily_loss_pct=0.99, max_weekly_loss_pct=0.99, max_monthly_loss_pct=0.99),
        day_start_equity=100_000,
    )  # max_drawdown_from_peak_pct defaults to 0 -> breaker disabled
    rm.update(110_000)
    assert not rm.update(70_000)  # -36% from peak, but the breaker is off
    assert not rm.halted


def test_catastrophic_stop_ignores_invalid_current_price():
    # A freshly-halted symbol can report current_price None/0; that must NOT trip the stop
    # (0 <= entry*0.9 is trivially true and would false-flatten a healthy position).
    rm = RiskManager(RiskConfig(catastrophic_stop_pct=0.10), 100_000)
    assert not rm.should_stop_out(entry_price=100, current_price=0)
    assert not rm.should_stop_out(entry_price=100, current_price=-5)
