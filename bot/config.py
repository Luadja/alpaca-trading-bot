"""Typed configuration loaded from environment / .env via pydantic-settings.

Only the Alpaca-facing layers import this. The strategy and risk layers use their
own plain dataclasses so they stay free of pydantic/Alpaca at test time.
"""

from __future__ import annotations

from pydantic import Field, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env", env_file_encoding="utf-8", extra="ignore"
    )

    # Credentials (env names match Alpaca's conventional APCA_* variables).
    api_key: str = Field(default="", validation_alias="APCA_API_KEY_ID")
    api_secret: str = Field(default="", validation_alias="APCA_API_SECRET_KEY")
    paper: bool = Field(default=True, validation_alias="ALPACA_PAPER")

    # Market data feed: "iex" (free real-time) or "delayed_sip" (free, 15-min delayed,
    # full market). "sip" requires the paid Algo Trader Plus plan.
    feed: str = Field(default="iex", validation_alias="ALPACA_FEED")

    # Diversified, survivorship-free default (broad + sector ETFs) — breadth is what turns
    # the thin per-symbol trend edge into a respectable portfolio.
    symbols: list[str] = Field(
        default=["SPY", "QQQ", "XLK", "XLF", "XLE", "XLV", "XLP", "XLU", "XLY", "IWM"],
        validation_alias="BOT_SYMBOLS",
    )
    timeframe: str = Field(default="1Day", validation_alias="BOT_TIMEFRAME")

    # Which strategy the live bot trades: "trend_momentum" (validated default) or
    # "stoch_rsi_mfi" (retired mean-reversion — kept for comparison).
    strategy: str = Field(default="trend_momentum", validation_alias="BOT_STRATEGY")

    # Market-regime gate: block new longs when SPY is below its long SMA (Faber-style
    # drawdown reducer). Conservative — only prevents entries, never adds risk. Default on.
    use_market_regime_filter: bool = Field(default=True, validation_alias="BOT_MARKET_REGIME_FILTER")
    market_regime_symbol: str = Field(default="SPY", validation_alias="BOT_MARKET_REGIME_SYMBOL")
    market_regime_sma: int = 200

    # Volatility-targeted position sizing (inverse-vol). Off by default — validate before
    # enabling, since the backtest harness uses full-equity sizing (doesn't model this).
    use_vol_targeting: bool = Field(default=False, validation_alias="BOT_VOL_TARGETING")
    vol_target_pct: float = Field(default=0.02, validation_alias="BOT_VOL_TARGET_PCT")

    # Trend strategy: enter when already in an uptrend (fast>slow) on startup, not only on a
    # fresh golden cross. Off by default — changes signal counts, so validate first.
    trend_enter_on_regime: bool = Field(default=False, validation_alias="BOT_TREND_ENTER_ON_REGIME")

    # Execution: use marketable-limit BUYs (price * (1+cap)) to bound slippage instead of
    # naked market orders. Exits stay market (guaranteed). A gap beyond the cap just won't
    # fill (no chasing). On by default; widen/disable for thin names.
    use_marketable_limit: bool = Field(default=True, validation_alias="BOT_MARKETABLE_LIMIT")
    slippage_cap_pct: float = Field(default=0.005, validation_alias="BOT_SLIPPAGE_CAP_PCT")

    # Absolute floor for bars before the bot acts. The live guard uses
    # max(warmup_bars, StochRsiMfiParams.min_bars), so changing trend_sma/timeframe can't
    # silently invalidate the regime gate — this is just a lower bound.
    warmup_bars: int = 220

    # Local SQLite ledger path + liveness heartbeat (read by the watchdog).
    ledger_path: str = "data/ledger.sqlite"
    heartbeat_path: str = "data/heartbeat.json"

    # Alerting (all optional — unset = log-only). Slack incoming webhook and/or SMTP email.
    alert_slack_webhook: str = Field(default="", validation_alias="ALERT_SLACK_WEBHOOK")
    alert_email_to: str = Field(default="", validation_alias="ALERT_EMAIL_TO")
    smtp_host: str = Field(default="", validation_alias="SMTP_HOST")
    smtp_port: int = Field(default=587, validation_alias="SMTP_PORT")
    smtp_user: str = Field(default="", validation_alias="SMTP_USER")
    smtp_password: str = Field(default="", validation_alias="SMTP_PASSWORD")

    @field_validator("symbols")
    @classmethod
    def _symbols_non_empty(cls, v: list[str]) -> list[str]:
        if not v:
            raise ValueError("BOT_SYMBOLS must list at least one symbol")
        return v

    @field_validator("slippage_cap_pct")
    @classmethod
    def _slippage_sane(cls, v: float) -> float:
        if not 0 < v < 0.1:  # negative would price the buy below market (never fills); >10% is absurd
            raise ValueError("BOT_SLIPPAGE_CAP_PCT must be between 0 and 0.1")
        return v

    def assert_keys(self) -> None:
        if not self.api_key or not self.api_secret:
            raise RuntimeError(
                "Missing Alpaca keys. Copy .env.example to .env and fill in your PAPER keys."
            )


def load_settings() -> Settings:
    return Settings()
