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
    assert Alerter(AlertConfig(discord_webhook_url="https://discord.test/wh")).enabled
    assert Alerter(AlertConfig(smtp_host="h", email_to="a@b.com")).enabled
    Alerter().notify("critical", "x", "y")  # unconfigured -> log-only, must not raise
    Alerter().activity("nothing configured")  # must not raise
    assert format_alert("critical", "x", "y") == "[CRITICAL] x — y"


def _fake_urlopen(sent):
    class _Resp:
        def __enter__(self): return self
        def __exit__(self, *a): return False
        def read(self): return b""

    def fake(req, timeout=None):
        sent.append((req.full_url, req.data))
        return _Resp()
    return fake


def test_discord_activity_and_alert_routing(monkeypatch):
    sent = []
    monkeypatch.setattr("urllib.request.urlopen", _fake_urlopen(sent))
    a = Alerter(AlertConfig(discord_webhook_url="https://discord.test/wh"))
    a.activity("🟢 BOUGHT 84 XLY @ ~$117.98")
    a.notify("critical", "kill switch", "daily -3%")
    assert len(sent) == 2  # both the activity feed and the alert hit the Discord webhook
    body = json.loads(sent[0][1])
    assert "content" in body and "BOUGHT" in body["content"]  # Discord uses {"content": ...}


def test_activity_goes_to_webhooks_not_email(monkeypatch):
    # Per-trade email would be spam: activity() must NOT send email even when SMTP is configured.
    calls = {"email": 0, "discord": 0}
    monkeypatch.setattr(Alerter, "_email", lambda self, s, b: calls.__setitem__("email", calls["email"] + 1))
    monkeypatch.setattr(Alerter, "_discord", lambda self, t: calls.__setitem__("discord", calls["discord"] + 1))
    a = Alerter(AlertConfig(discord_webhook_url="https://d/x", smtp_host="h", email_to="a@b.com"))
    a.activity("🔴 SOLD 84 XLY — P&L +12.34 (+0.12%)")
    assert calls == {"email": 0, "discord": 1}


def test_discord_sends_user_agent_header(monkeypatch):
    # Discord's API is behind Cloudflare and returns HTTP 403 without a User-Agent header.
    seen = {}

    class _Resp:
        def __enter__(self): return self
        def __exit__(self, *a): return False
        def read(self): return b""

    def fake(req, timeout=None):
        seen["ua"] = req.get_header("User-agent")
        return _Resp()
    monkeypatch.setattr("urllib.request.urlopen", fake)
    Alerter(AlertConfig(discord_webhook_url="https://discord.test/wh"))._discord("hi")
    assert seen["ua"]  # must be a non-empty User-Agent, or Discord 403s


def test_alerter_never_raises_on_transport_failure(monkeypatch):
    def boom(req, timeout=None):
        raise RuntimeError("network down")
    monkeypatch.setattr("urllib.request.urlopen", boom)
    a = Alerter(AlertConfig(discord_webhook_url="https://discord.test/wh"))
    a.activity("x")            # transport failure must be swallowed
    a.notify("critical", "y")  # ditto — must never break the trade path


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


def test_watchdog_startup_grace_no_flatten_on_missing_heartbeat(tmp_path):
    # Cold start: the bot hasn't written its first heartbeat yet. Within the grace, a MISSING
    # heartbeat must NOT flatten a (possibly healthy) account.
    broker = _Broker(is_open=True, leftover={"AAPL": 5})
    fired = check_once(broker, _Alerter(), str(tmp_path / "none.json"), 180, LOG,
                       already_fired=False, startup_grace=120.0, elapsed=10.0)
    assert fired is False and broker.flattened == 0  # within grace -> no flatten


def test_watchdog_flattens_after_grace_elapses(tmp_path):
    # Past the grace, a still-missing heartbeat is a genuine dead bot -> flatten.
    broker = _Broker(is_open=True)
    fired = check_once(broker, _Alerter(), str(tmp_path / "none.json"), 180, LOG,
                       already_fired=False, startup_grace=120.0, elapsed=200.0)
    assert fired is True and broker.flattened == 1


def test_watchdog_crypto_armed_when_stock_market_closed(tmp_path):
    # Crypto trades 24/7: even though the STOCK clock says closed, a stale heartbeat must still
    # flatten — otherwise the dead-man's switch would be off nights/weekends for a crypto bot.
    p = str(tmp_path / "hb.json")
    broker = _Broker(is_open=False)
    _write_stale(p)
    fired = check_once(broker, _Alerter(), p, 180, LOG, already_fired=False, is_crypto=True)
    assert fired is True and broker.flattened == 1


def test_watchdog_stock_mode_defers_when_market_closed(tmp_path):
    # Sanity: STOCK mode preserves the existing 'don't act outside market hours' behavior.
    p = str(tmp_path / "hb.json")
    broker = _Broker(is_open=False)
    _write_stale(p)
    fired = check_once(broker, _Alerter(), p, 180, LOG, already_fired=False, is_crypto=False)
    assert fired is False and broker.flattened == 0


def test_watchdog_leftover_heartbeat_within_grace_no_flatten(tmp_path):
    # A heartbeat OLDER than the watchdog's own uptime is a prior-session leftover (the file
    # persists on disk), not the bot we just launched. Within the grace it must NOT flatten,
    # else a normal morning restart liquidates carried positions before the bot's first write.
    p = str(tmp_path / "hb.json")
    _write_stale(p)  # ~1h old
    broker = _Broker(is_open=True, leftover={"AAPL": 5})
    fired = check_once(broker, _Alerter(), p, 180, LOG,
                       already_fired=False, startup_grace=9999.0, elapsed=1.0)  # age >> elapsed
    assert fired is False and broker.flattened == 0


def test_watchdog_existing_stale_heartbeat_flattens_when_from_this_session(tmp_path):
    # A heartbeat YOUNGER than the watchdog's uptime was written this session, then went stale
    # -> a genuine crash; flatten even inside the grace window.
    p = str(tmp_path / "hb.json")
    _write_stale(p)  # ~1h old (3600s)
    broker = _Broker(is_open=True)
    fired = check_once(broker, _Alerter(), p, 180, LOG,
                       already_fired=False, startup_grace=9999.0, elapsed=5000.0)  # age < elapsed
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
