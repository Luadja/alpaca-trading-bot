import json
import logging
from datetime import datetime, timedelta, timezone
from pathlib import Path

from bot.alerting import AlertConfig, Alerter, format_alert
from bot.heartbeat import heartbeat_age_seconds, read_heartbeat, write_heartbeat
from scripts.watchdog import check_once

LOG = logging.getLogger("test")


def test_heartbeat_roundtrip(tmp_path):
    p = str(tmp_path / "hb.json")
    assert read_heartbeat(p) is None  # missing file
    write_heartbeat(p, {"halted": False})
    hb = read_heartbeat(p)
    assert hb["halted"] is False and "ts" in hb
    assert heartbeat_age_seconds(hb) < 5


def test_heartbeat_age():
    old = (datetime.now(timezone.utc) - timedelta(minutes=10)).isoformat()
    assert heartbeat_age_seconds({"ts": old}) > 500
    assert heartbeat_age_seconds({}) is None  # no timestamp


def test_alerting_enabled_and_noop():
    assert not Alerter().enabled
    assert Alerter(AlertConfig(slack_webhook_url="https://hooks.example/x")).enabled
    assert Alerter(AlertConfig(smtp_host="h", email_to="a@b.com")).enabled
    Alerter().notify("critical", "x", "y")  # unconfigured -> log-only, must not raise
    assert format_alert("critical", "x", "y") == "[CRITICAL] x — y"


class _Broker:
    def __init__(self, is_open=True, flatten_raises=False, open_raises=False,
                 flatten_clears=True, leftover=None):
        self._open = is_open
        self.flattened = 0
        self._flatten_raises = flatten_raises
        self._open_raises = open_raises
        self._flatten_clears = flatten_clears
        self._positions = dict(leftover or {})

    def is_market_open(self):
        if self._open_raises:
            raise RuntimeError("api down")
        return self._open

    def flatten_all(self):
        self.flattened += 1
        if self._flatten_raises:
            raise RuntimeError("flatten boom")
        if self._flatten_clears:
            self._positions = {}

    def positions(self):
        return dict(self._positions)


class _Alerter:
    def __init__(self):
        self.count = 0

    def notify(self, *a, **k):
        self.count += 1


def _write_stale(path):
    old = (datetime.now(timezone.utc) - timedelta(hours=1)).isoformat()
    Path(path).write_text(json.dumps({"ts": old}))


def test_watchdog_flattens_once_on_stale(tmp_path):
    p = str(tmp_path / "hb.json")
    broker, alerter = _Broker(is_open=True), _Alerter()
    _write_stale(p)
    fired = check_once(broker, alerter, p, 180, LOG, already_fired=False)
    assert fired is True and broker.flattened == 1 and alerter.count >= 1
    # still stale, already fired -> no second flatten (no spam)
    fired = check_once(broker, alerter, p, 180, LOG, already_fired=True)
    assert fired is True and broker.flattened == 1


def test_watchdog_missing_heartbeat_flattens(tmp_path):
    broker = _Broker(is_open=True)
    fired = check_once(broker, _Alerter(), str(tmp_path / "none.json"), 180, LOG, already_fired=False)
    assert fired is True and broker.flattened == 1


def test_watchdog_fresh_resets_latch(tmp_path):
    p = str(tmp_path / "hb.json")
    write_heartbeat(p, {"halted": False})
    broker = _Broker(is_open=True)
    fired = check_once(broker, _Alerter(), p, 180, LOG, already_fired=True)
    assert fired is False and broker.flattened == 0  # fresh -> re-arm, no flatten


def test_watchdog_skips_when_market_closed(tmp_path):
    broker = _Broker(is_open=False)
    fired = check_once(broker, _Alerter(), str(tmp_path / "none.json"), 180, LOG, already_fired=False)
    assert fired is False and broker.flattened == 0  # never act outside market hours


def test_watchdog_no_latch_on_flatten_failure(tmp_path):
    p = str(tmp_path / "hb.json")
    _write_stale(p)
    broker, alerter = _Broker(is_open=True, flatten_raises=True), _Alerter()
    fired = check_once(broker, alerter, p, 180, LOG, already_fired=False)
    assert fired is False and broker.flattened == 1  # failed flatten must not latch
    check_once(broker, alerter, p, 180, LOG, already_fired=False)
    assert broker.flattened == 2  # retries on the next tick


def test_watchdog_no_latch_when_positions_remain(tmp_path):
    p = str(tmp_path / "hb.json")
    _write_stale(p)
    broker = _Broker(is_open=True, flatten_clears=False, leftover={"AAPL": 5})
    # confirm_timeout=0 -> read positions once and decide (don't sleep the full bounded poll).
    fired = check_once(broker, _Alerter(), p, 180, LOG, already_fired=False, confirm_timeout=0.0)
    assert fired is False and broker.flattened == 1  # incomplete flatten must not latch


class _AsyncBroker:
    """Flatten submits async closes: positions clear only after a couple of poll reads, like
    Alpaca's close_all_positions where fills land seconds after the call returns."""

    def __init__(self, clears_after=2):
        self._open = True
        self.flattened = 0
        self._reads = 0
        self._clears_after = clears_after
        self._positions = {"AAPL": 5}

    def is_market_open(self):
        return self._open

    def flatten_all(self):
        self.flattened += 1

    def positions(self):
        self._reads += 1
        if self._reads > self._clears_after:
            self._positions = {}
        return dict(self._positions)


def test_watchdog_confirms_flat_after_async_fills(tmp_path):
    # The bounded poll must wait out the async close fills and then latch (not false-alarm
    # 'positions remain' on the submit-latency right after flatten_all()).
    p = str(tmp_path / "hb.json")
    _write_stale(p)
    broker = _AsyncBroker(clears_after=2)
    fired = check_once(broker, _Alerter(), p, 180, LOG, already_fired=False,
                       confirm_timeout=5.0, confirm_poll=0.01)
    assert fired is True and broker.flattened == 1  # confirmed flat once the fills landed


def test_watchdog_degraded_on_market_state_error(tmp_path):
    broker, alerter = _Broker(open_raises=True), _Alerter()
    fired = check_once(broker, alerter, str(tmp_path / "x.json"), 180, LOG, already_fired=False)
    assert fired is False and broker.flattened == 0 and alerter.count >= 1  # alert, don't fail open


def test_watchdog_future_timestamp_is_stale(tmp_path):
    p = str(tmp_path / "hb.json")
    future = (datetime.now(timezone.utc) + timedelta(hours=1)).isoformat()
    Path(p).write_text(json.dumps({"ts": future}))
    broker = _Broker(is_open=True)
    fired = check_once(broker, _Alerter(), p, 180, LOG, already_fired=False)
    assert fired is True and broker.flattened == 1  # clock-skew future ts treated as stale
