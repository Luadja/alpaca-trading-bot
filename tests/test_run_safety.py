"""Focused unit tests for the two safety-critical execution paths reworked in 0cf8d81:

  - TradingBot._flatten_catastrophic — the catastrophic hard-stop liquidation, with the
    kill-switch `halted` defer and the `live_held` cap that stops it overselling a long.
  - TradingBot._retry_coid — the terminal-unfilled retry that must NOT stack a second live
    order while a prior retry still rests on the book.

Both are exercised with fake broker/ledger doubles (mirroring tests/test_observability.py),
constructing the bot via __new__ so __init__'s heavy network setup is bypassed.
"""

import logging
import threading
import types

from alpaca.trading.enums import OrderSide

from bot.run import TradingBot

LOG = logging.getLogger("test")


class _Order:
    """A broker open-order double. side is a plain string; _order_side() lower-cases it."""

    def __init__(self, symbol, side, qty=0):
        self.symbol = symbol
        self.side = side
        self.qty = qty


class _SubmittedOrder:
    def __init__(self, id="broker-1"):
        self.id = id


class _Broker:
    def __init__(self, open_orders=None, position_qty=0.0, open_raises=False, posqty_raises=False):
        self._open_orders = list(open_orders or [])
        self._position_qty = position_qty
        self._open_raises = open_raises
        self._posqty_raises = posqty_raises
        self.submitted = []  # every submit_market(symbol, qty, side, coid) call, in order

    def open_orders(self):
        if self._open_raises:
            raise RuntimeError("api down")
        return list(self._open_orders)

    def position_qty(self, symbol):
        if self._posqty_raises:
            raise RuntimeError("api down")
        return self._position_qty

    def submit_market(self, symbol, qty, side, client_order_id=None):
        self.submitted.append({"symbol": symbol, "qty": qty, "side": side, "coid": client_order_id})
        return _SubmittedOrder()


class _Ledger:
    def __init__(self, states=None):
        self._states = dict(states or {})  # coid -> {"status": ..., "filled_qty": ...}
        self.intents = []  # record_intent calls
        self.submissions = []  # mark_submitted calls

    def record_intent(self, coid, symbol, side, qty, reason=""):
        self.intents.append({"coid": coid, "symbol": symbol, "side": side, "qty": qty})
        return True

    def mark_submitted(self, coid, broker_order_id):
        self.submissions.append((coid, broker_order_id))

    def order_state(self, coid):
        return self._states.get(coid)


def _bot(broker, ledger=None, halted=False):
    bot = TradingBot.__new__(TradingBot)  # skip __init__'s network/broker/ledger setup
    bot.broker = broker
    bot.ledger = ledger if ledger is not None else _Ledger()
    bot.risk = types.SimpleNamespace(halted=halted)
    bot._lock = threading.RLock()
    bot.log = LOG
    bot.is_crypto = False  # stock-path tests; crypto routing is exercised separately
    return bot


# --- _flatten_catastrophic ------------------------------------------------------------------

def test_flatten_catastrophic_defers_when_halted():
    # Kill switch owns the liquidation (flatten_all cancels + closes everything): the per-symbol
    # catastrophic SELL must defer, submitting NOTHING, so it can't oversell into a short.
    broker = _Broker(open_orders=[], position_qty=10.0)
    bot = _bot(broker, halted=True)

    assert bot._flatten_catastrophic("AAPL", 10.0) == (0.0, "halted")
    assert broker.submitted == []  # no competing SELL
    assert bot.ledger.intents == []  # not even recorded — re-checked atomically under the lock


def test_flatten_catastrophic_caps_sell_to_live_held():
    # The qty is step()'s stale snapshot (10); a concurrent flatten already cut the live position
    # to 3. We must sell only the 3 we still hold, never the stale 10.
    broker = _Broker(open_orders=[], position_qty=3.0)
    bot = _bot(broker)
    bot._await_fill = lambda coid: (3.0, "filled")

    filled, status = bot._flatten_catastrophic("AAPL", 10.0)
    assert (filled, status) == (3.0, "filled")
    assert len(broker.submitted) == 1
    assert broker.submitted[0]["qty"] == 3.0  # capped to live_held, not the stale 10
    assert broker.submitted[0]["side"] == OrderSide.SELL


def test_flatten_catastrophic_does_not_restack_a_working_sell():
    # A non-terminal SELL already covers the full long (e.g. a prior stop resting through an LULD
    # halt). Resending a unique-coid SELL each cycle would oversell into a SHORT — so let it work.
    broker = _Broker(open_orders=[_Order("AAPL", "sell", qty=10)], position_qty=10.0)
    bot = _bot(broker)

    assert bot._flatten_catastrophic("AAPL", 10.0) == (0.0, "working")
    assert broker.submitted == []
    assert bot.ledger.intents == []


def test_flatten_catastrophic_resubmits_after_terminal_prior_stop():
    # A rejected/terminal prior stop is no longer in open_orders(), so the working sum drops to 0
    # and the flatten must resubmit. The resting MSFT sell and the AAPL *buy* must NOT count
    # toward AAPL's working SELL volume (symbol+side filter).
    broker = _Broker(
        open_orders=[_Order("MSFT", "sell", qty=10), _Order("AAPL", "buy", qty=10)],
        position_qty=10.0,
    )
    bot = _bot(broker)
    bot._await_fill = lambda coid: (10.0, "filled")

    filled, status = bot._flatten_catastrophic("AAPL", 10.0)
    assert (filled, status) == (10.0, "filled")
    assert len(broker.submitted) == 1
    assert broker.submitted[0]["qty"] == 10.0
    assert len(bot.ledger.intents) == 1  # intent recorded before the network submit


# --- _retry_coid ----------------------------------------------------------------------------

_BAR = "2026-06-17"


def test_retry_coid_uses_deterministic_id_when_prior_fresh_live_or_filled():
    coid = TradingBot._coid("AAPL", "sell", _BAR)

    # fresh: no prior record at all
    assert _bot(_Broker(), _Ledger())._retry_coid("AAPL", "sell", _BAR) == (coid, False)
    # filled: the decision was carried out — never retry
    filled = _Ledger({coid: {"status": "filled", "filled_qty": 10}})
    assert _bot(_Broker(), filled)._retry_coid("AAPL", "sell", _BAR) == (coid, False)
    # live (non-terminal): still working — the deterministic id stays idempotent
    live = _Ledger({coid: {"status": "new", "filled_qty": 0}})
    assert _bot(_Broker(), live)._retry_coid("AAPL", "sell", _BAR) == (coid, False)


def test_retry_coid_issues_fresh_retry_after_terminal_unfilled_with_nothing_working():
    # Prior deterministic attempt rejected (0 fill) and stuck terminal; nothing rests on the book
    # -> a wanted order would be abandoned all day. Issue a FRESH retry id.
    coid = TradingBot._coid("AAPL", "sell", _BAR)
    ledger = _Ledger({coid: {"status": "rejected", "filled_qty": 0}})
    bot = _bot(_Broker(open_orders=[]), ledger)

    new_coid, is_retry = bot._retry_coid("AAPL", "sell", _BAR)
    assert is_retry is True
    assert new_coid.startswith(coid + "-r") and new_coid != coid


def test_retry_coid_suppresses_retry_while_a_working_order_still_rests():
    # Prior deterministic attempt is terminal-unfilled, BUT an earlier retry is still working for
    # (AAPL, sell). Stacking another live order risks two fills (double exit). Fall back to the
    # deterministic id so the caller's already_submitted() blocks the dupe.
    coid = TradingBot._coid("AAPL", "sell", _BAR)
    ledger = _Ledger({coid: {"status": "canceled", "filled_qty": 0}})
    bot = _bot(_Broker(open_orders=[_Order("AAPL", "sell", qty=10)]), ledger)

    assert bot._retry_coid("AAPL", "sell", _BAR) == (coid, False)


def test_retry_coid_conservatively_suppresses_when_open_orders_raises():
    # Can't confirm the book is clear -> assume something may rest; don't risk a second live order.
    coid = TradingBot._coid("AAPL", "sell", _BAR)
    ledger = _Ledger({coid: {"status": "expired", "filled_qty": 0}})
    bot = _bot(_Broker(open_raises=True), ledger)

    assert bot._retry_coid("AAPL", "sell", _BAR) == (coid, False)
