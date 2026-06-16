"""Pure Plotly chart builders for the dashboard (no Streamlit — unit-testable).

Strategy-agnostic: it plots whatever indicator columns ``compute_signals`` produced
(SMAs as price overlays; StochRSI/MFI in a lower oscillator panel) plus buy/sell
markers from the ``signal`` column.
"""

from __future__ import annotations

import pandas as pd
import plotly.graph_objects as go
from plotly.subplots import make_subplots

# Columns drawn as price overlays vs. in the lower oscillator panel.
_OVERLAYS = [
    ("sma_fast", "SMA fast"),
    ("sma_slow", "SMA slow"),
    ("sma_trend", "trend SMA"),
    ("sma_regime", "regime SMA"),
]
_OSCILLATORS = [("stochrsi_k", "StochRSI %K"), ("stochrsi_d", "StochRSI %D"), ("mfi", "MFI")]


def build_price_figure(df: pd.DataFrame, symbol: str, strategy_name: str = "") -> go.Figure:
    """Price + indicator overlays + entry/exit markers, with an oscillator panel if present."""
    oscillators = [(c, label) for c, label in _OSCILLATORS if c in df.columns]
    rows = 2 if oscillators else 1
    fig = make_subplots(
        rows=rows,
        cols=1,
        shared_xaxes=True,
        row_heights=[0.7, 0.3] if rows == 2 else [1.0],
        vertical_spacing=0.04,
    )

    fig.add_trace(
        go.Scatter(x=df.index, y=df["close"], name="Close", line=dict(color="#2563eb", width=1.6)),
        row=1, col=1,
    )
    for col, label in _OVERLAYS:
        if col in df.columns:
            fig.add_trace(go.Scatter(x=df.index, y=df[col], name=label, line=dict(width=1)), row=1, col=1)

    if "signal" in df.columns:
        buys, sells = df[df["signal"] == 1], df[df["signal"] == -1]
        fig.add_trace(
            go.Scatter(x=buys.index, y=buys["close"], mode="markers", name="Buy",
                       marker=dict(symbol="triangle-up", size=12, color="#16a34a")),
            row=1, col=1,
        )
        fig.add_trace(
            go.Scatter(x=sells.index, y=sells["close"], mode="markers", name="Sell",
                       marker=dict(symbol="triangle-down", size=12, color="#dc2626")),
            row=1, col=1,
        )

    for col, label in oscillators:
        fig.add_trace(go.Scatter(x=df.index, y=df[col], name=label, line=dict(width=1)), row=2, col=1)
    if oscillators:
        for level in (20, 80):
            fig.add_hline(y=level, line=dict(color="gray", dash="dot", width=0.6), row=2, col=1)

    title = symbol + (f"  —  {strategy_name}" if strategy_name else "")
    fig.update_layout(
        title=title,
        height=560,
        margin=dict(l=10, r=10, t=44, b=10),
        legend=dict(orientation="h", y=1.02, yanchor="bottom"),
        template="plotly_white",
        hovermode="x unified",
    )
    return fig
