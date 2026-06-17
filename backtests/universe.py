"""Backtest symbol universes + a point-in-time loader to control survivorship bias.

The hardcoded mega-cap names are survivorship-BIASED (selected *because* they won), so a
long-only backtest on them is juiced — results are an upper bound, not an estimate. The
ETF universe is survivorship-free (the ETF persists; index reconstitution happens inside
it), and a point-in-time constituent CSV (symbol,start,end) lets you backtest honestly on
the names that were actually investable in each window.
"""

from __future__ import annotations

import csv
from datetime import date
from pathlib import Path

# Survivorship-BIASED: today's winners. Reproduces the earlier (biased) PLAN.md numbers.
MEGACAP_BIASED = [
    "AAPL", "MSFT", "NVDA", "SPY", "QQQ", "IWM",
    "KO", "PG", "JNJ", "XLU", "XLP", "VZ", "INTC", "DIS", "WMT",
]

# Survivorship-FREE: broad-market + sector ETFs (each ETF persists, reconstitution is
# internal), so a long-only backtest here isn't hand-picked to winners. NOTE: these are ALL
# US equity, so they're highly correlated (~0.8-0.95) — little real diversification.
ETF_UNIVERSE = [
    "SPY", "QQQ", "IWM", "DIA",
    "XLK", "XLF", "XLE", "XLV", "XLI", "XLP", "XLU", "XLY", "XLB", "XLC", "XLRE",
]

# Survivorship-FREE, CROSS-ASSET: deliberately diversified across 6 low-correlation classes.
# Trend-following has its strongest, best-documented edge applied ACROSS uncorrelated asset
# classes (the managed-futures / time-series-momentum result) — more independent trends to
# ride and lower portfolio drawdown than an all-equity set. All have history well before the
# 2018 backtest window.
MULTI_ASSET = [
    "SPY", "QQQ", "IWM",   # US equity
    "EFA", "EEM",          # international: developed + emerging
    "TLT", "IEF",          # Treasuries: long + intermediate (the key equity diversifier)
    "LQD", "HYG",          # credit: investment-grade + high-yield
    "GLD",                 # gold
    "DBC",                 # broad commodities
    "VNQ",                 # REITs
]


def load_universe_csv(path: str) -> tuple[list[str], dict[str, tuple[date, date]]]:
    """Load a point-in-time universe CSV (rows: ``symbol,start,end`` ISO dates; a header
    row named 'symbol' is skipped). Returns (symbols, windows) where windows maps each
    symbol to the (start, end) it was investable, for clipping its bars."""
    symbols: list[str] = []
    windows: dict[str, tuple[date, date]] = {}
    with open(path, newline="", encoding="utf-8") as f:
        for row in csv.reader(f):
            if not row or row[0].strip().lower() in ("", "symbol"):
                continue
            sym = row[0].strip().upper()
            start = date.fromisoformat(row[1].strip()) if len(row) > 1 and row[1].strip() else date.min
            end = date.fromisoformat(row[2].strip()) if len(row) > 2 and row[2].strip() else date.max
            symbols.append(sym)
            windows[sym] = (start, end)
    return symbols, windows


def resolve_universe(spec: str) -> tuple[list[str], dict[str, tuple[date, date]] | None, bool]:
    """Resolve ``spec`` ('etf' | 'megacap' | a CSV path) to (symbols, windows|None, is_biased)."""
    if spec == "etf":
        return ETF_UNIVERSE, None, False
    if spec in ("multiasset", "multi_asset", "broad"):
        return MULTI_ASSET, None, False
    if spec == "megacap":
        return MEGACAP_BIASED, None, True
    if Path(spec).is_file():
        symbols, windows = load_universe_csv(spec)
        return symbols, windows, False
    raise ValueError(f"--universe must be 'etf', 'megacap', or a CSV path; got {spec!r}")
