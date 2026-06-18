"""Market-mode routing in run.py: crypto vs stock order/data/position handling.

The crypto symbol-format robustness here is load-bearing: if a held BTC position (returned by
Alpaca as 'BTCUSD') failed to match the managed 'BTC/USD', the bot would think it was flat and
re-buy forever / never exit. These tests pin that down without any network."""

from __future__ import annotations

import types

from alpaca.trading.enums import OrderSide

from bot.run import TradingBot


class _Broker:
    def __init__(self, positions=None):
        self._positions = positions or {}
        self.calls = []  # every order submission, in order

    def submit_crypto_market(self, symbol, qty, side, client_order_id=None):
        self.calls.append(("crypto_market", symbol, qty, side, client_order_id))
        return types.SimpleNamespace(id="cx")

    def submit_market(self, symbol, qty, side, client_order_id=None):
        self.calls.append(("market", symbol, qty, side, client_order_id))
        return types.SimpleNamespace(id="mk")

    def submit_limit(self, symbol, qty, side, limit_price, client_order_id=None):
        self.calls.append(("limit", symbol, qty, side, limit_price, client_order_id))
        return types.SimpleNamespace(id="lm")

    def positions(self):
        return dict(self._positions)

    def position_qty(self, symbol):
        return self._positions.get(symbol, 0.0)


def _bot(broker, *, is_crypto, slippage=0.005):
    bot = TradingBot.__new__(TradingBot)
    bot.broker = broker
    bot.is_crypto = is_crypto
    bot.settings = types.SimpleNamespace(slippage_cap_pct=slippage)
    return bot


# --- symbol normalization -------------------------------------------------------------------

def test_norm_is_slash_insensitive_and_identity_for_stocks():
    assert TradingBot._norm("BTC/USD") == "BTCUSD"
    assert TradingBot._norm("btc/usd") == "BTCUSD"
    assert TradingBot._norm("SPY") == "SPY"


def test_market_open_always_true_for_crypto():
    assert _bot(_Broker(), is_crypto=True)._market_open() is True


# --- order routing --------------------------------------------------------------------------

def test_crypto_buy_routes_to_crypto_market_fractional():
    broker = _Broker()
    _bot(broker, is_crypto=True)._submit_order("BTC/USD", 0.0123, OrderSide.BUY, "c1", ref=62000)
    assert broker.calls == [("crypto_market", "BTC/USD", 0.0123, OrderSide.BUY, "c1")]


def test_crypto_sell_routes_to_crypto_market():
    broker = _Broker()
    _bot(broker, is_crypto=True)._submit_order("ETH/USD", 0.5, OrderSide.SELL, "c2")
    assert broker.calls == [("crypto_market", "ETH/USD", 0.5, OrderSide.SELL, "c2")]


def test_stock_whole_qty_buy_uses_marketable_limit():
    broker = _Broker()
    _bot(broker, is_crypto=False)._submit_order("SPY", 3, OrderSide.BUY, "c3", ref=100.0)
    kind, sym, qty, side, limit, coid = broker.calls[0]
    assert kind == "limit" and sym == "SPY" and qty == 3 and limit == 100.5  # 100 * (1+0.005)


def test_stock_fractional_buy_falls_back_to_market():
    broker = _Broker()
    _bot(broker, is_crypto=False)._submit_order("SPY", 1.5, OrderSide.BUY, "c4", ref=100.0)
    assert broker.calls[0][0] == "market"  # fractional can't be a limit


def test_stock_sell_is_always_market():
    broker = _Broker()
    _bot(broker, is_crypto=False)._submit_order("SPY", 2, OrderSide.SELL, "c5", ref=100.0)
    assert broker.calls[0][0] == "market"


# --- position matching (the load-bearing crypto-format robustness) --------------------------

def test_live_position_qty_matches_crypto_without_slash():
    # Alpaca returns the position as 'BTCUSD'; the managed symbol is 'BTC/USD'. Must still match.
    broker = _Broker(positions={"BTCUSD": 0.5})
    assert _bot(broker, is_crypto=True)._live_position_qty("BTC/USD") == 0.5


def test_live_position_qty_matches_crypto_with_slash():
    broker = _Broker(positions={"BTC/USD": 0.5})
    assert _bot(broker, is_crypto=True)._live_position_qty("BTC/USD") == 0.5


def test_live_position_qty_stock_exact_match():
    broker = _Broker(positions={"SPY": 7.0})
    assert _bot(broker, is_crypto=False)._live_position_qty("SPY") == 7.0


# --- deterministic coid is config-format-independent ----------------------------------------

def test_coid_is_normalized_across_slash_format():
    # Same logical decision must hash to the same id whether the symbol is 'BTC/USD' or 'BTCUSD'
    # so idempotency holds even if BOT_SYMBOLS formatting changes between restarts.
    assert TradingBot._coid("BTC/USD", "buy", "2026-06-18") == TradingBot._coid("BTCUSD", "buy", "2026-06-18")
    # ...but different side / bar still differ.
    assert TradingBot._coid("BTC/USD", "buy", "2026-06-18") != TradingBot._coid("BTC/USD", "sell", "2026-06-18")
