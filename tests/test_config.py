from bot.config import Settings


def _settings(**over):
    # _env_file=None ignores the local .env so we test code defaults, not the user's config.
    return Settings(_env_file=None, api_key="x", api_secret="y", **over)


def test_execution_and_universe_defaults():
    s = _settings()
    assert s.use_marketable_limit is True
    assert s.slippage_cap_pct == 0.005
    assert s.strategy == "trend_momentum"
    assert s.use_market_regime_filter is True
    assert s.use_vol_targeting is False  # opt-in, validate first
    assert len(s.symbols) >= 5  # diversified default universe


def test_empty_symbols_rejected_via_env(monkeypatch):
    monkeypatch.setenv("BOT_SYMBOLS", "[]")
    try:
        Settings(_env_file=None, api_key="x", api_secret="y")
    except Exception as exc:  # pydantic ValidationError
        assert "symbol" in str(exc).lower()
        return
    raise AssertionError("empty BOT_SYMBOLS should be rejected")


def test_feed_validator_accepts_sip_iex_rejects_delayed_sip(monkeypatch):
    for good, want in (("sip", "sip"), ("IEX", "iex")):  # accepted (and normalized)
        monkeypatch.setenv("ALPACA_FEED", good)
        assert Settings(_env_file=None, api_key="x", api_secret="y").feed == want
        monkeypatch.delenv("ALPACA_FEED", raising=False)
    # 'delayed_sip' is rejected by the Alpaca bars endpoint, so the validator must reject it.
    for bad in ("delayed_sip", "sipp", ""):
        monkeypatch.setenv("ALPACA_FEED", bad)
        try:
            Settings(_env_file=None, api_key="x", api_secret="y")
        except Exception as exc:
            assert "feed" in str(exc).lower()
        else:
            raise AssertionError(f"feed={bad!r} should be rejected")
        monkeypatch.delenv("ALPACA_FEED", raising=False)


def test_negative_slippage_cap_rejected(monkeypatch):
    monkeypatch.setenv("BOT_SLIPPAGE_CAP_PCT", "-0.01")  # would price the buy below market
    try:
        Settings(_env_file=None, api_key="x", api_secret="y")
    except Exception as exc:
        assert "slippage" in str(exc).lower()
        return
    raise AssertionError("negative slippage cap should be rejected")
