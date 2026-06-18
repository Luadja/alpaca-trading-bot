"""Trading strategies. Strategy code is PURE: data in, signals out, no broker calls."""

from bot.strategy.base import Strategy
from bot.strategy.mean_reversion import MeanReversionParams, MeanReversionStrategy
from bot.strategy.stoch_rsi_mfi import StochRsiMfiParams, StochRsiMfiStrategy, compute_signals
from bot.strategy.trend_momentum import TrendMomentumParams, TrendMomentumStrategy

# Live strategy registry: name -> Strategy class (constructed with default params).
STRATEGIES: dict[str, type[Strategy]] = {
    "stoch_rsi_mfi": StochRsiMfiStrategy,
    "trend_momentum": TrendMomentumStrategy,
    "mean_reversion": MeanReversionStrategy,
}


def make_strategy(name: str) -> Strategy:
    """Build a strategy by name (with its default params). Used by the live bot."""
    try:
        return STRATEGIES[name]()
    except KeyError:
        raise ValueError(f"Unknown strategy {name!r}; choices: {list(STRATEGIES)}") from None


__all__ = [
    "Strategy",
    "StochRsiMfiStrategy",
    "StochRsiMfiParams",
    "compute_signals",
    "TrendMomentumStrategy",
    "TrendMomentumParams",
    "MeanReversionStrategy",
    "MeanReversionParams",
    "STRATEGIES",
    "make_strategy",
]
