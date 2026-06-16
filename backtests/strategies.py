"""Strategy registry for the backtest/validation tooling.

Each entry exposes a uniform interface so param_sweep.py and validate.py can run
ANY strategy: the pure ``signals(df, params)`` function, a ``default()`` param set,
a ``grid()`` of param variants to sweep, and a ``pkey(params)`` label fn.

To add a strategy: implement compute_signals + a Params dataclass behind the
Strategy interface, then register it here.
"""

from __future__ import annotations

from bot.strategy.stoch_rsi_mfi import StochRsiMfiParams
from bot.strategy.stoch_rsi_mfi import compute_signals as stoch_signals
from bot.strategy.trend_momentum import TrendMomentumParams
from bot.strategy.trend_momentum import compute_signals as trend_signals


# --- stoch_rsi_mfi -----------------------------------------------------------
def _stoch_grid() -> list[StochRsiMfiParams]:
    """Symmetric bands x divergence on/off, filter pinned OFF (raw oscillator)."""
    grid = []
    for stoch_os in (20, 30, 40):
        for mfi_os in (25, 35, 45):
            for div_required in (False, True):
                grid.append(
                    StochRsiMfiParams(
                        stoch_oversold=stoch_os,
                        stoch_overbought=100 - stoch_os,
                        mfi_oversold=mfi_os,
                        mfi_overbought=100 - mfi_os,
                        use_divergence=True,
                        divergence_required=div_required,
                        use_trend_filter=False,
                    )
                )
    return grid


def _stoch_pkey(p: StochRsiMfiParams) -> str:
    trend = f"tf{int(p.trend_sma)}" if p.use_trend_filter else "tf-off"
    return (
        f"stoch{int(p.stoch_oversold)}/{int(p.stoch_overbought)} "
        f"mfi{int(p.mfi_oversold)}/{int(p.mfi_overbought)} "
        f"div={'Y' if p.divergence_required else 'N'} {trend}"
    )


# --- trend_momentum ----------------------------------------------------------
def _trend_grid() -> list[TrendMomentumParams]:
    """The two strong SMA pairs x trailing-stop {off, 10%, 15%, 20%}. 3x4 = 12 sets."""
    grid = []
    for fast, slow in ((20, 100), (50, 100), (50, 200)):
        for trail in (0.0, 0.10, 0.15, 0.20):
            grid.append(
                TrendMomentumParams(
                    fast_sma=fast,
                    slow_sma=slow,
                    use_trailing_stop=trail > 0,
                    trail_pct=trail if trail > 0 else 0.15,
                )
            )
    return grid


def _trend_pkey(p: TrendMomentumParams) -> str:
    trail = f"trail{int(p.trail_pct * 100)}" if p.use_trailing_stop else "trail-off"
    return f"sma{int(p.fast_sma)}/{int(p.slow_sma)} {trail}"


REGISTRY = {
    "stoch_rsi_mfi": {
        "signals": stoch_signals,
        "default": lambda: StochRsiMfiParams(use_trend_filter=False),
        "grid": _stoch_grid,
        "pkey": _stoch_pkey,
    },
    "trend_momentum": {
        "signals": trend_signals,
        "default": TrendMomentumParams,
        "grid": _trend_grid,
        "pkey": _trend_pkey,
    },
}
