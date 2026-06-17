"""Logging configuration: rotating file + stdout so logs survive restarts and are
captured by systemd/journald or Docker when you eventually deploy.
"""

from __future__ import annotations

import json
import logging
import sys
from datetime import datetime, timezone
from logging.handlers import RotatingFileHandler
from pathlib import Path

_CONFIGURED = False


class _JsonFormatter(logging.Formatter):
    """One JSON object per line — parseable by Loki/CloudWatch/etc."""

    def format(self, record: logging.LogRecord) -> str:
        out = {
            "ts": datetime.fromtimestamp(record.created, timezone.utc).isoformat(),
            "level": record.levelname,
            "logger": record.name,
            "msg": record.getMessage(),
        }
        if record.exc_info:
            out["exc"] = self.formatException(record.exc_info)
        if record.stack_info:
            out["stack"] = self.formatStack(record.stack_info)
        return json.dumps(out)


def setup_logging(
    level: int = logging.INFO, log_file: str = "logs/bot.log", json_format: bool = False
) -> logging.Logger:
    global _CONFIGURED
    logger = logging.getLogger("bot")
    if _CONFIGURED:
        return logger

    logger.setLevel(level)
    fmt: logging.Formatter = (
        _JsonFormatter()
        if json_format
        else logging.Formatter(
            "%(asctime)s | %(levelname)-7s | %(name)s | %(message)s",
            datefmt="%Y-%m-%d %H:%M:%S",
        )
    )

    # UTF-8 everywhere so emoji / em-dashes in messages neither raise nor mojibake on Windows
    # (the console + default file encoding are cp1252 there).
    if hasattr(sys.stdout, "reconfigure"):
        try:
            sys.stdout.reconfigure(encoding="utf-8", errors="backslashreplace")
        except Exception:  # pragma: no cover - best effort
            pass

    stream = logging.StreamHandler(sys.stdout)
    stream.setFormatter(fmt)
    logger.addHandler(stream)

    Path(log_file).parent.mkdir(parents=True, exist_ok=True)
    file_handler = RotatingFileHandler(log_file, maxBytes=5_000_000, backupCount=5, encoding="utf-8")
    file_handler.setFormatter(fmt)
    logger.addHandler(file_handler)

    logger.propagate = False
    _CONFIGURED = True
    return logger
