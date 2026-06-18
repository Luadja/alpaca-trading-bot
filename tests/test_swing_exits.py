from bot.strategy.swing_exits import ExitParams, check_exit

WIDE = dict(take_profit_pct=0.99, stop_loss_pct=0.99, trail_pct=0.0, max_bars=10_000)


def test_stop():
    ex, px, why = check_exit(100, 100, 1, 101, 97, 98, ExitParams(**{**WIDE, "stop_loss_pct": 0.025}))
    assert ex and why == "stop" and abs(px - 97.5) < 1e-9


def test_target():
    ex, px, why = check_exit(100, 100, 1, 105, 99, 104, ExitParams(**{**WIDE, "take_profit_pct": 0.04}))
    assert ex and why == "target" and abs(px - 104.0) < 1e-9


def test_trailing():
    ex, px, why = check_exit(100, 120, 5, 119, 116, 117, ExitParams(**{**WIDE, "trail_pct": 0.03}))
    assert ex and why == "trail" and abs(px - 116.4) < 1e-9


def test_time():
    ex, px, why = check_exit(100, 101, 48, 101, 100, 100.5, ExitParams(**{**WIDE, "max_bars": 48}))
    assert ex and why == "time" and px == 100.5


def test_stop_taken_before_target_when_bar_spans_both():
    # bar low hits the stop AND bar high hits the target -> conservative: stop first
    ex, px, why = check_exit(100, 100, 1, 105, 97, 100, ExitParams(take_profit_pct=0.04, stop_loss_pct=0.025,
                                                                   trail_pct=0.0, max_bars=10_000))
    assert ex and why == "stop"


def test_hold_when_nothing_hit():
    ex, px, why = check_exit(100, 100, 1, 101, 99, 100, ExitParams(0.04, 0.025, 0.03, 48))
    assert not ex and px is None
