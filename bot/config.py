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

    # Minimum bars required before the bot will act (covers RSI/StochRSI/MFI warmup
    # plus the divergence lookback, with headroom for Wilder smoothing to converge).
    warmup_bars: int = 150

    # Local SQLite ledger path.
    ledger_path: str = "data/ledger.sqlite"

    def assert_keys(self) -> None:
        if not self.api_key or not self.api_secret:
            raise RuntimeError(
                "Missing Alpaca keys. Copy .env.example to .env and fill in your PAPER keys."
            )


def load_settings() -> Settings:
    return Settings()
