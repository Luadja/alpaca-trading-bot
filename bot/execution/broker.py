"""Thin wrapper around alpaca-py's TradingClient.

Key safety property: every order carries a unique ``client_order_id`` so a retry
after a network timeout cannot create a duplicate. Alpaca does NOT guarantee it
rejects duplicate orders, so the bot must own idempotency. On a timeout, VERIFY
via the API — never blindly resend.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass

from alpaca.trading.client import TradingClient
from alpaca.trading.enums import OrderSide, QueryOrderStatus, TimeInForce
from alpaca.trading.requests import (
    GetOrdersRequest,
    LimitOrderRequest,
    MarketOrderRequest,
)

from bot.config import Settings


@dataclass
class AccountSnapshot:
    equity: float
    cash: float
    buying_power: float


class Broker:
    def __init__(self, settings: Settings) -> None:
        settings.assert_keys()
        self.client = TradingClient(settings.api_key, settings.api_secret, paper=settings.paper)
        self.paper = settings.paper

    # --- reads -------------------------------------------------------------
    def account(self) -> AccountSnapshot:
        acct = self.client.get_account()
        return AccountSnapshot(
            equity=float(acct.equity),
            cash=float(acct.cash),
            buying_power=float(acct.buying_power),
        )

    def list_positions(self):
        """Raw position objects (have .symbol, .qty, .market_value)."""
        return self.client.get_all_positions()

    def positions(self) -> dict[str, float]:
        """symbol -> signed quantity for all open positions."""
        return {p.symbol: float(p.qty) for p in self.list_positions()}

    def position_qty(self, symbol: str) -> float:
        return self.positions().get(symbol, 0.0)

    def open_orders(self):
        return self.client.get_orders(filter=GetOrdersRequest(status=QueryOrderStatus.OPEN))

    # --- writes ------------------------------------------------------------
    @staticmethod
    def new_client_order_id(prefix: str = "bot") -> str:
        return f"{prefix}-{uuid.uuid4().hex[:20]}"

    def submit_market(
        self,
        symbol: str,
        qty: float,
        side: OrderSide,
        client_order_id: str | None = None,
    ):
        req = MarketOrderRequest(
            symbol=symbol,
            qty=qty,
            side=side,
            time_in_force=TimeInForce.DAY,
            client_order_id=client_order_id or self.new_client_order_id(),
        )
        return self.client.submit_order(order_data=req)

    def submit_limit(
        self,
        symbol: str,
        qty: float,
        side: OrderSide,
        limit_price: float,
        client_order_id: str | None = None,
    ):
        req = LimitOrderRequest(
            symbol=symbol,
            qty=qty,
            side=side,
            limit_price=round(limit_price, 2),
            time_in_force=TimeInForce.DAY,
            client_order_id=client_order_id or self.new_client_order_id(),
        )
        return self.client.submit_order(order_data=req)

    def buy(self, symbol: str, qty: float, client_order_id: str | None = None):
        return self.submit_market(symbol, qty, OrderSide.BUY, client_order_id)

    def sell(self, symbol: str, qty: float, client_order_id: str | None = None):
        return self.submit_market(symbol, qty, OrderSide.SELL, client_order_id)

    def close_position(self, symbol: str):
        """Liquidate the full position in one symbol (handles fractional qty)."""
        return self.client.close_position(symbol)

    # --- kill switch -------------------------------------------------------
    def flatten_all(self) -> None:
        """Cancel open orders and close every position. Used by the kill switch."""
        self.client.close_all_positions(cancel_orders=True)
