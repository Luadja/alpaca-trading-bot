"""Typed configuration loaded from environment / .env via pydantic-settings.

Only the Alpaca-facing layers import this. The strategy and risk layers use their
own plain dataclasses so they stay free of pydantic/Alpaca at test time.
"""

from __future__ import annotations

from pydantic import Field
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

    symbols: list[str] = Field(default=["AAPL", "MSFT"], validation_alias="BOT_SYMBOLS")
    timeframe: str = Field(default="1Day", validation_alias="BOT_TIMEFRAME")

    # Which strategy the live bot trades: "trend_momentum" (validated default) or
    # "stoch_rsi_mfi" (retired mean-reversion — kept for comparison).
    strategy: str = Field(default="trend_momentum", validation_alias="BOT_STRATEGY")

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

    def assert_keys(self) -> None:
        if not self.api_key or not self.api_secret:
            raise RuntimeError(
                "Missing Alpaca keys. Copy .env.example to .env and fill in your PAPER keys."
            )


def load_settings() -> Settings:
    return Settings()
