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
    """Fast/slow SMA pairs x regime filter on/off. 2x2x2 = 8 sets."""
    grid = []
    for fast in (20, 50):
        for slow in (100, 200):
            for regime in (False, True):
                grid.append(
                    TrendMomentumParams(
                        fast_sma=fast, slow_sma=slow, use_regime_filter=regime, regime_sma=200
                    )
                )
    return grid


def _trend_pkey(p: TrendMomentumParams) -> str:
    return f"sma{int(p.fast_sma)}/{int(p.slow_sma)} reg={'Y' if p.use_regime_filter else 'N'}"


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
