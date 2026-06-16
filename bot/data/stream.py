"""Live websocket bar stream (skeleton).

IMPORTANT websocket gotchas (Alpaca):
  * Only ONE connection per data endpoint is allowed. A crashed bot that reconnects
    before the old socket is dropped hits "connection limit exceeded". Run a single
    stream and fan out internally.
  * Auth must complete within 10s of connecting.
  * Free (Basic/IEX) plan caps subscriptions at 30 symbols.
  * StockDataStream.run() owns its own asyncio event loop and BLOCKS — which is why
    a live bot must be a persistent process (cron/serverless cannot keep it alive).

For "local for now" the bot defaults to a polling loop (see bot/run.py). Wire this
stream in when you want event-driven, intrabar reactions.
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable

from alpaca.data.enums import DataFeed
from alpaca.data.live import StockDataStream

from bot.config import Settings

BarHandler = Callable[[dict], Awaitable[None]]


class BarStream:
    def __init__(self, settings: Settings) -> None:
        settings.assert_keys()
        feed = DataFeed.IEX if settings.feed.lower() != "sip" else DataFeed.SIP
        self.stream = StockDataStream(settings.api_key, settings.api_secret, feed=feed)
        self.symbols = settings.symbols

    def on_bar(self, handler: BarHandler) -> None:
        async def _wrapped(bar) -> None:  # alpaca-py passes a Bar model
            await handler(
                {
                    "symbol": bar.symbol,
                    "timestamp": bar.timestamp,
                    "open": bar.open,
                    "high": bar.high,
                    "low": bar.low,
                    "close": bar.close,
                    "volume": bar.volume,
                }
            )

        self.stream.subscribe_bars(_wrapped, *self.symbols)

    def run(self) -> None:
        """Blocking — runs the websocket on its own asyncio loop."""
        self.stream.run()
