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
