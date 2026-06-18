"""Thin wrapper around alpaca-py's TradingClient.

Two safety properties:
  * Every order carries a deterministic ``client_order_id`` so a retry after a
    network timeout cannot create a duplicate — Alpaca rejects a duplicate id, and
    we treat that rejection as "already landed" and fetch the existing order.
  * All calls go through ``_retry`` (exponential backoff + jitter) so a single 429
    (200 req/min limit) or transient network error doesn't abort the trading cycle.
"""

from __future__ import annotations

import random
import time
import uuid
from dataclasses import dataclass

from alpaca.common.exceptions import APIError
from alpaca.trading.client import TradingClient
from alpaca.trading.enums import OrderSide, QueryOrderStatus, TimeInForce
from alpaca.trading.requests import (
    GetOrdersRequest,
    LimitOrderRequest,
    MarketOrderRequest,
)

from bot.config import Settings

_RETRYABLE_STATUS = {408, 429, 500, 502, 503, 504}


def _retry(fn, *, tries: int = 5, base: float = 1.0):
    """Call fn() with capped exponential backoff + jitter on transient errors.

    Safe only for idempotent operations: reads, or writes keyed by a deterministic
    client_order_id (which Alpaca dedupes). 429s and 5xx/network blips are retried;
    other API errors propagate immediately.
    """
    last_exc: Exception | None = None
    for attempt in range(tries):
        try:
            return fn()
        except APIError as exc:
            if getattr(exc, "status_code", None) not in _RETRYABLE_STATUS:
                raise
            last_exc = exc
        except (ConnectionError, TimeoutError, OSError) as exc:
            last_exc = exc
        if attempt < tries - 1:  # no point sleeping after the last attempt — we're about to raise
            time.sleep(min(30.0, base * (2 ** attempt) + random.uniform(0.0, 0.5)))
    assert last_exc is not None
    raise last_exc


def _is_duplicate_coid(exc: APIError) -> bool:
    if getattr(exc, "status_code", None) not in (409, 422):
        return False
    msg = str(exc).lower()
    return "client_order_id" in msg or "duplicate" in msg


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

    # --- reads (freely retryable) ------------------------------------------
    def account(self) -> AccountSnapshot:
        acct = _retry(self.client.get_account)
        return AccountSnapshot(
            equity=float(acct.equity),
            cash=float(acct.cash),
            buying_power=float(acct.buying_power),
        )

    def list_positions(self):
        """Raw position objects (have .symbol, .qty, .market_value, .avg_entry_price, .current_price)."""
        return _retry(self.client.get_all_positions)

    def positions(self) -> dict[str, float]:
        """symbol -> signed quantity for all open positions."""
        return {p.symbol: float(p.qty) for p in self.list_positions()}

    def position_qty(self, symbol: str) -> float:
        return self.positions().get(symbol, 0.0)

    def open_orders(self):
        return _retry(
            lambda: self.client.get_orders(filter=GetOrdersRequest(status=QueryOrderStatus.OPEN))
        )

    def market_clock(self):
        return _retry(self.client.get_clock)

    def is_market_open(self) -> bool:
        return bool(self.market_clock().is_open)

    def get_order(self, client_order_id: str):
        """Fetch an order by its client_order_id; None if it never landed (404)."""
        def do():
            try:
                return self.client.get_order_by_client_id(client_order_id)
            except APIError as exc:
                if getattr(exc, "status_code", None) == 404:
                    return None
                raise

        return _retry(do)

    # --- writes ------------------------------------------------------------
    @staticmethod
    def new_client_order_id(prefix: str = "bot") -> str:
        return f"{prefix}-{uuid.uuid4().hex[:20]}"

    def _submit(self, req, coid: str):
        """Submit an order, retrying transient errors. The deterministic coid makes a
        retried submit safe: a duplicate-id rejection means a prior attempt landed, so
        fetch and return that order instead of resubmitting."""
        def do():
            try:
                return self.client.submit_order(order_data=req)
            except APIError as exc:
                if _is_duplicate_coid(exc):
                    return self.client.get_order_by_client_id(coid)
                raise

        try:
            return _retry(do)
        except Exception:
            # Lost-response edge: the order may have been created server-side before the
            # error. Reconcile by coid before declaring failure so a created order is never lost.
            existing = self.get_order(coid)
            if existing is not None:
                return existing
            raise

    def submit_market(
        self,
        symbol: str,
        qty: float,
        side: OrderSide,
        client_order_id: str | None = None,
    ):
        coid = client_order_id or self.new_client_order_id()
        req = MarketOrderRequest(
            symbol=symbol, qty=qty, side=side, time_in_force=TimeInForce.DAY, client_order_id=coid
        )
        return self._submit(req, coid)

    def submit_limit(
        self,
        symbol: str,
        qty: float,
        side: OrderSide,
        limit_price: float,
        client_order_id: str | None = None,
    ):
        coid = client_order_id or self.new_client_order_id()
        req = LimitOrderRequest(
            symbol=symbol,
            qty=qty,
            side=side,
            limit_price=round(limit_price, 2),
            time_in_force=TimeInForce.DAY,
            client_order_id=coid,
        )
        return self._submit(req, coid)

    def submit_crypto_market(self, symbol: str, qty: float, side: OrderSide,
                             client_order_id: str | None = None):
        """Crypto market order. Crypto requires GTC time-in-force (DAY is stock-only) and
        supports fractional qty natively. Routes through the same idempotent _submit."""
        coid = client_order_id or self.new_client_order_id()
        req = MarketOrderRequest(
            symbol=symbol, qty=qty, side=side, time_in_force=TimeInForce.GTC, client_order_id=coid
        )
        return self._submit(req, coid)

    def submit_crypto_limit(self, symbol: str, qty: float, side: OrderSide, limit_price: float,
                            client_order_id: str | None = None):
        """Crypto limit order (GTC, fractional qty). NOTE: not currently on the live path (the
        bot submits crypto as market GTC); kept for completeness. Rounds the limit adaptively —
        2dp for >=$1 pairs, more decimals for sub-dollar pairs — so a low-priced coin isn't
        mis-priced by 2dp rounding."""
        coid = client_order_id or self.new_client_order_id()
        px = round(limit_price, 2) if limit_price >= 1.0 else round(limit_price, 6)
        req = LimitOrderRequest(
            symbol=symbol, qty=qty, side=side, limit_price=px,
            time_in_force=TimeInForce.GTC, client_order_id=coid,
        )
        return self._submit(req, coid)

    def buy(self, symbol: str, qty: float, client_order_id: str | None = None):
        return self.submit_market(symbol, qty, OrderSide.BUY, client_order_id)

    def sell(self, symbol: str, qty: float, client_order_id: str | None = None):
        return self.submit_market(symbol, qty, OrderSide.SELL, client_order_id)

    def close_position(self, symbol: str):
        """Liquidate the full position in one symbol (handles fractional qty).
        Tolerates a 404 (already flat) so it's safe to call defensively."""
        def do():
            try:
                return self.client.close_position(symbol)
            except APIError as exc:
                if getattr(exc, "status_code", None) == 404:
                    return None  # no position to close
                raise

        return _retry(do)

    # --- kill switch -------------------------------------------------------
    def flatten_all(self) -> None:
        """Cancel open orders and close every position. Used by the kill switch."""
        _retry(lambda: self.client.close_all_positions(cancel_orders=True))
