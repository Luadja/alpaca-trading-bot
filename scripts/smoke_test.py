"""Smoke test — verify your PAPER connection, data access, and signal output.

Read-only by default. Pass --place-test-order to place a single 1-share market
order (refused unless ALPACA_PAPER=true).

    python -m scripts.smoke_test
    python -m scripts.smoke_test --symbol MSFT --place-test-order
"""

from __future__ import annotations

import argparse

from bot.config import load_settings
from bot.data.historical import HistoricalData, parse_timeframe
from bot.execution.broker import Broker
from bot.strategy import StochRsiMfiStrategy


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--symbol", default="AAPL")
    ap.add_argument("--place-test-order", action="store_true")
    args = ap.parse_args()

    settings = load_settings()
    print(f"Paper mode: {settings.paper}   Feed: {settings.feed}")

    broker = Broker(settings)
    acct = broker.account()
    print(
        f"Account: equity=${acct.equity:,.2f}  cash=${acct.cash:,.2f}  "
        f"buying_power=${acct.buying_power:,.2f}"
    )
    print("Open positions:", broker.positions() or "none")

    data = HistoricalData(settings)
    df = data.get_bars(args.symbol, parse_timeframe(settings.timeframe), lookback_days=400, use_cache=False)
    print(f"\nFetched {len(df)} bars for {args.symbol}.")
    if not df.empty:
        print(f"Latest close: {df['close'].iloc[-1]:.2f}")
        decision = StochRsiMfiStrategy().generate(df, args.symbol)
        print(
            f"Latest signal: {decision.signal.value} | confidence={decision.confidence} "
            f"| {decision.reason}"
        )

    if args.place_test_order:
        if not settings.paper:
            raise SystemExit("Refusing to place a test order on a LIVE account.")
        order = broker.buy(args.symbol, 1)
        print(f"\nPlaced PAPER BUY 1 {args.symbol}: id={order.id} status={order.status}")


if __name__ == "__main__":
    main()
