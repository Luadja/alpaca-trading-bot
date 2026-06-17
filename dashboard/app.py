"""Streamlit dashboard for the Alpaca trading bot.

Read-only view of the paper/live account, positions, the active strategy's signals on
a price chart, and the local order ledger.

Run from the project root:
    streamlit run dashboard/app.py
"""

from __future__ import annotations

import pathlib
import sqlite3
import sys

# Make `import bot` work regardless of where streamlit is launched from.
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent))

import pandas as pd  # noqa: E402
import streamlit as st  # noqa: E402

from bot.config import load_settings  # noqa: E402
from bot.data.historical import HistoricalData, parse_timeframe  # noqa: E402
from bot.execution.broker import Broker  # noqa: E402
from bot.strategy import make_strategy  # noqa: E402
from dashboard.charts import build_price_figure  # noqa: E402

st.set_page_config(page_title="Alpaca Bot Dashboard", layout="wide")
settings = load_settings()


@st.cache_resource
def _broker() -> Broker:
    return Broker(settings)


@st.cache_resource
def _data() -> HistoricalData:
    return HistoricalData(settings)


@st.cache_data(ttl=60)
def account_snapshot() -> dict:
    a = _broker().account()
    return {"equity": a.equity, "cash": a.cash, "bp": a.buying_power}


@st.cache_data(ttl=60)
def positions_df() -> pd.DataFrame:
    rows = []
    for p in _broker().list_positions():
        rows.append(
            {
                "symbol": p.symbol,
                "qty": float(p.qty),
                "avg_entry": float(p.avg_entry_price),
                "price": float(p.current_price),
                "mkt_value": float(p.market_value),
                "unreal_$": float(p.unrealized_pl),
                "unreal_%": 100 * float(p.unrealized_plpc),
            }
        )
    return pd.DataFrame(rows)


@st.cache_data(ttl=300)
def bars(symbol: str, lookback: int) -> pd.DataFrame:
    tf = parse_timeframe(settings.timeframe)
    # Use the bot's configured feed so the dashboard's SMAs/signals match what the bot trades.
    # Fall back to sip (free, full-market for >15min-old bars) then IEX. NB: delayed_sip is
    # rejected by the bars endpoint, so it's not a fallback.
    fallbacks = ["sip", "iex"]
    feeds = [settings.feed] + [f for f in fallbacks if f != settings.feed]
    last_exc: Exception | None = None
    for feed in feeds:
        try:
            return _data().get_bars(symbol, tf, lookback_days=lookback, use_cache=False, feed=feed)
        except Exception as exc:  # subscription error on a paid-only feed -> try the next
            last_exc = exc
    raise last_exc


def recent_orders(limit: int = 25) -> pd.DataFrame:
    try:
        con = sqlite3.connect(settings.ledger_path)
        df = pd.read_sql_query(
            "SELECT created_at, symbol, side, qty, status, reason "
            "FROM orders ORDER BY created_at DESC LIMIT ?",
            con,
            params=(limit,),
        )
        con.close()
        return df
    except Exception:
        return pd.DataFrame()


# --- sidebar ----------------------------------------------------------------
st.sidebar.header("Controls")
symbol = st.sidebar.selectbox("Symbol", settings.symbols)
lookback = st.sidebar.slider("Lookback (days)", 300, 1500, 750, step=50)
mode = "🟢 PAPER" if settings.paper else "🔴 LIVE"
st.sidebar.markdown(
    f"**Mode:** {mode}  \n**Strategy:** `{settings.strategy}`  \n**Timeframe:** {settings.timeframe}"
)
if st.sidebar.button("↻ Refresh data"):
    st.cache_data.clear()

# --- header / account -------------------------------------------------------
st.title("📈 Alpaca Trading Bot — Dashboard")
try:
    acct = account_snapshot()
    c1, c2, c3 = st.columns(3)
    c1.metric("Equity", f"${acct['equity']:,.2f}")
    c2.metric("Cash", f"${acct['cash']:,.2f}")
    c3.metric("Buying power", f"${acct['bp']:,.2f}")
except Exception as exc:
    st.error(f"Could not load account — check your keys in .env. ({exc})")

# --- positions --------------------------------------------------------------
st.subheader("Open positions")
try:
    pos = positions_df()
    if pos.empty:
        st.caption("No open positions.")
    else:
        st.dataframe(pos, use_container_width=True, hide_index=True)
except Exception as exc:
    st.warning(f"Positions unavailable: {exc}")

# --- chart ------------------------------------------------------------------
st.subheader(f"{symbol} — price, indicators & signals")
try:
    df = bars(symbol, lookback)
    if df.empty:
        st.warning("No bars returned for this symbol/timeframe.")
    else:
        strat = make_strategy(settings.strategy)
        if len(df) < strat.params.min_bars:
            st.warning(
                f"Only {len(df)} bars; the strategy needs {strat.params.min_bars}. "
                "Signals near the start will be empty."
            )
        sig = strat.signals(df)
        decision = strat.generate(df, symbol)
        d1, d2, d3 = st.columns(3)
        d1.metric("Latest signal", decision.signal.value)
        d2.metric("Confidence", f"{decision.confidence:.2f}")
        d3.metric("Last close", f"${decision.price:,.2f}")
        st.caption(decision.reason)
        st.plotly_chart(build_price_figure(sig, symbol, settings.strategy), use_container_width=True)
except Exception as exc:
    st.error(f"Chart error: {exc}")

# --- ledger -----------------------------------------------------------------
st.subheader("Recent orders (local ledger)")
orders = recent_orders()
if orders.empty:
    st.caption("No orders recorded yet — run the bot to populate the ledger.")
else:
    st.dataframe(orders, use_container_width=True, hide_index=True)

st.caption("Read-only view · paper-first · not financial advice.")
