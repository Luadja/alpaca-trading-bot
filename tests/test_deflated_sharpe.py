import math

from backtests.deflated_sharpe import deflated_sharpe, expected_max_sharpe


def test_norm_helpers_via_expected_max():
    # With identical trials there's no spread, so the luck benchmark is 0.
    assert expected_max_sharpe([0.5, 0.5, 0.5]) == 0.0
    # More dispersed trials -> higher expected max under the null.
    spread = expected_max_sharpe([-1.0, 0.0, 1.0, 2.0, -0.5, 0.5])
    assert spread > 0.0


def test_dsr_high_when_few_trials_long_sample():
    # A strong Sharpe, only a couple of trials, long sample -> high confidence.
    dsr = deflated_sharpe(observed_sharpe=1.5, trial_sharpes=[1.5, 0.2], n_obs=2000)
    assert 0.9 <= dsr <= 1.0


def test_dsr_low_when_many_noisy_trials():
    # A modest Sharpe picked from many widely-varying trials -> the luck benchmark is high,
    # so confidence should be low.
    trials = [(-1) ** i * (i % 5) * 0.5 for i in range(50)]
    dsr = deflated_sharpe(observed_sharpe=0.4, trial_sharpes=trials, n_obs=300)
    assert dsr < 0.5


def test_dsr_bounds():
    d = deflated_sharpe(0.3, [0.3, 0.1, 0.2], 500)
    assert 0.0 <= d <= 1.0 and math.isfinite(d)
