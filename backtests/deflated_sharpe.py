"""Deflated Sharpe Ratio (Bailey & López de Prado) — a multiple-testing correction.

Picking the best of N backtested configs and reporting its Sharpe is selection bias: even
skill-less strategies produce a positive *maximum* Sharpe that grows with N. The DSR asks
whether the chosen Sharpe beats what that selection process would produce by luck.

Pure stdlib (no scipy): normal CDF via math.erf, inverse-CDF via Acklam's approximation.
The inputs here are approximate (trial Sharpes from the grid, T from the sample length,
normality assumed), so treat the output as a guard rail, not a precise p-value.
"""

from __future__ import annotations

import math

_EULER_MASCHERONI = 0.5772156649015329


def _norm_cdf(x: float) -> float:
    return 0.5 * (1.0 + math.erf(x / math.sqrt(2.0)))


def _norm_ppf(p: float) -> float:
    """Inverse standard-normal CDF (Acklam's rational approximation)."""
    if not 0.0 < p < 1.0:
        return -math.inf if p <= 0.0 else math.inf
    a = [-3.969683028665376e1, 2.209460984245205e2, -2.759285104469687e2,
         1.383577518672690e2, -3.066479806614716e1, 2.506628277459239]
    b = [-5.447609879822406e1, 1.615858368580409e2, -1.556989798598866e2,
         6.680131188771972e1, -1.328068155288572e1]
    c = [-7.784894002430293e-3, -3.223964580411365e-1, -2.400758277161838,
         -2.549732539343734, 4.374664141464968, 2.938163982698783]
    d = [7.784695709041462e-3, 3.224671290700398e-1, 2.445134137142996, 3.754408661907416]
    plow, phigh = 0.02425, 1 - 0.02425
    if p < plow:
        q = math.sqrt(-2 * math.log(p))
        return (((((c[0]*q+c[1])*q+c[2])*q+c[3])*q+c[4])*q+c[5]) / ((((d[0]*q+d[1])*q+d[2])*q+d[3])*q+1)
    if p > phigh:
        q = math.sqrt(-2 * math.log(1 - p))
        return -(((((c[0]*q+c[1])*q+c[2])*q+c[3])*q+c[4])*q+c[5]) / ((((d[0]*q+d[1])*q+d[2])*q+d[3])*q+1)
    q = p - 0.5
    r = q * q
    return (((((a[0]*r+a[1])*r+a[2])*r+a[3])*r+a[4])*r+a[5])*q / (((((b[0]*r+b[1])*r+b[2])*r+b[3])*r+b[4])*r+1)


def expected_max_sharpe(trial_sharpes: list[float]) -> float:
    """SR0: the Sharpe you'd expect from the BEST of N skill-less trials, given their spread."""
    trials = [s for s in trial_sharpes if s == s]  # drop NaN
    n = len(trials)
    if n < 2:
        return 0.0
    mean = sum(trials) / n
    var = sum((s - mean) ** 2 for s in trials) / (n - 1)
    sigma = math.sqrt(var)
    g = _EULER_MASCHERONI
    return sigma * ((1 - g) * _norm_ppf(1 - 1.0 / n) + g * _norm_ppf(1 - 1.0 / (n * math.e)))


def deflated_sharpe(
    observed_sharpe: float, trial_sharpes: list[float], n_obs: int,
    skew: float = 0.0, kurtosis: float = 3.0,
) -> float:
    """Probability the observed Sharpe is real after correcting for N trials (0..1).

    Compares observed_sharpe to expected_max_sharpe (the luck benchmark) and scales by the
    sample length n_obs. >= ~0.95 is the usual bar to bless a config.
    """
    sr0 = expected_max_sharpe(trial_sharpes)
    if n_obs < 2:
        return 0.0
    denom = math.sqrt(max(1e-9, 1 - skew * observed_sharpe + (kurtosis - 1) / 4 * observed_sharpe**2))
    return _norm_cdf((observed_sharpe - sr0) * math.sqrt(n_obs - 1) / denom)
