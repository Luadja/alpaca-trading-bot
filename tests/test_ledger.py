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

    # Persistent KV state (used for kill-switch/equity anchors).
    assert led.get_state("risk") is None
    led.set_state("risk", '{"a": 1}')
    assert led.get_state("risk") == '{"a": 1}'
    led.set_state("risk", '{"a": 2}')  # upsert
    assert led.get_state("risk") == '{"a": 2}'

    led.close()
