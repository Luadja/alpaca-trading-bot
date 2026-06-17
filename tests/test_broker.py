import pytest

import bot.execution.broker as broker_mod
from bot.execution.broker import _retry


def test_retry_does_not_sleep_after_final_attempt(monkeypatch):
    # Regression: _retry used to sleep even after the last attempt, adding pointless latency
    # before re-raising. It should sleep only BETWEEN attempts (tries - 1 times).
    sleeps: list[float] = []
    monkeypatch.setattr(broker_mod.time, "sleep", lambda s: sleeps.append(s))
    calls = {"n": 0}

    def always_fail():
        calls["n"] += 1
        raise TimeoutError("transient")

    with pytest.raises(TimeoutError):
        _retry(always_fail, tries=3, base=0.0)
    assert calls["n"] == 3   # exhausted all attempts
    assert len(sleeps) == 2  # slept between attempts only, never after the final one


def test_retry_no_sleep_on_immediate_success(monkeypatch):
    sleeps: list[float] = []
    monkeypatch.setattr(broker_mod.time, "sleep", lambda s: sleeps.append(s))
    assert _retry(lambda: 42, tries=5) == 42
    assert sleeps == []
