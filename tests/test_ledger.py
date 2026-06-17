from bot.state import Ledger


def test_record_intent_is_idempotent(tmp_path):
    led = Ledger(str(tmp_path / "ledger.sqlite"))
    coid = "bot-deadbeef"

    assert led.record_intent(coid, "AAPL", "buy", 10, "first") is True
    # Same client_order_id (same logical decision) is dropped, not re-inserted.
    assert led.record_intent(coid, "AAPL", "buy", 10, "retry") is False

    assert led.already_submitted(coid) is False
    led.mark_submitted(coid, "broker-1")
    assert led.already_submitted(coid) is True

    led.close()


def test_fill_pending_and_state(tmp_path):
    led = Ledger(str(tmp_path / "ledger.sqlite"))

    led.record_intent("c1", "AAPL", "buy", 10, "x")
    assert [r["client_order_id"] for r in led.pending_orders()] == ["c1"]  # intended is pending
    led.mark_submitted("c1", "b1")
    assert [r["client_order_id"] for r in led.pending_orders()] == ["c1"]  # submitted still pending

    led.record_fill("c1", 10, 150.0, "filled")
    assert led.pending_orders() == []  # filled is terminal
    assert led.already_submitted("c1")
    assert led.status_counts().get("filled") == 1

    # Persistent KV state (used for kill-switch/equity anchors).
    assert led.get_state("risk") is None
    led.set_state("risk", '{"a": 1}')
    assert led.get_state("risk") == '{"a": 1}'
    led.set_state("risk", '{"a": 2}')  # upsert
    assert led.get_state("risk") == '{"a": 2}'

    led.close()


def test_order_state_distinguishes_unfilled_terminal_from_filled(tmp_path):
    # Foundation for the terminal-zero-fill retry: order_state must expose status + filled_qty
    # so a rejected (0-fill) attempt can be retried while a filled one is left alone.
    led = Ledger(str(tmp_path / "ledger.sqlite"))
    assert led.order_state("nope") is None  # unknown coid

    led.record_intent("rej", "AAPL", "sell", 10, "exit")
    led.mark_submitted("rej", "b1")
    led.record_fill("rej", 0, None, "rejected")
    st = led.order_state("rej")
    assert st["status"] == "rejected" and st["filled_qty"] == 0

    led.record_intent("ok", "AAPL", "sell", 10, "exit")
    led.record_fill("ok", 10, 150.0, "filled")
    st = led.order_state("ok")
    assert st["status"] == "filled" and st["filled_qty"] == 10

    led.close()
