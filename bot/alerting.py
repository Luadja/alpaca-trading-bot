"""Operator alerting — kill-switch trips, order rejects, errors, heartbeat loss.

Backends are config-driven and OPTIONAL: with nothing configured, alerts degrade to
log-only, and delivery failures are swallowed (the bot must never break because a
Slack webhook is down). Stdlib only (urllib / smtplib) — no new dependencies.
"""

from __future__ import annotations

import json
import logging
import smtplib
import urllib.request
from dataclasses import dataclass
from email.message import EmailMessage

log = logging.getLogger("bot.alerts")

_LOG_LEVELS = {"info": logging.INFO, "warning": logging.WARNING, "critical": logging.CRITICAL}


def format_alert(level: str, event: str, detail: str = "") -> str:
    return f"[{level.upper()}] {event}" + (f" — {detail}" if detail else "")


@dataclass
class AlertConfig:
    slack_webhook_url: str = ""
    smtp_host: str = ""
    smtp_port: int = 587
    smtp_user: str = ""
    smtp_password: str = ""
    email_to: str = ""


class Alerter:
    def __init__(self, config: AlertConfig | None = None) -> None:
        self.config = config or AlertConfig()

    @property
    def enabled(self) -> bool:
        c = self.config
        return bool(c.slack_webhook_url or (c.smtp_host and c.email_to))

    def notify(self, level: str, event: str, detail: str = "") -> None:
        """Best-effort alert: always logs, sends to configured backends, never raises."""
        text = format_alert(level, event, detail)
        log.log(_LOG_LEVELS.get(level, logging.INFO), "ALERT %s", text)
        if not self.enabled:
            return
        try:
            if self.config.slack_webhook_url:
                self._slack(text)
            if self.config.smtp_host and self.config.email_to:
                self._email(f"[bot] {level}: {event}", text)
        except Exception:  # alerting must never take down the bot
            log.exception("alert delivery failed (event=%s)", event)

    def _slack(self, text: str) -> None:
        payload = json.dumps({"text": text}).encode("utf-8")
        req = urllib.request.Request(
            self.config.slack_webhook_url, data=payload,
            headers={"Content-Type": "application/json"},
        )
        with urllib.request.urlopen(req, timeout=10) as resp:  # noqa: S310 (config URL)
            resp.read()

    def _email(self, subject: str, body: str) -> None:
        c = self.config
        msg = EmailMessage()
        msg["Subject"] = subject
        msg["From"] = c.smtp_user or c.email_to  # a real address; MTAs reject bare strings
        msg["To"] = c.email_to
        msg.set_content(body)
        if c.smtp_port == 465:  # implicit TLS
            with smtplib.SMTP_SSL(c.smtp_host, c.smtp_port, timeout=15) as smtp:
                if c.smtp_user:
                    smtp.login(c.smtp_user, c.smtp_password)
                smtp.send_message(msg)
        else:  # STARTTLS (587)
            with smtplib.SMTP(c.smtp_host, c.smtp_port, timeout=15) as smtp:
                smtp.starttls()
                if c.smtp_user:
                    smtp.login(c.smtp_user, c.smtp_password)
                smtp.send_message(msg)


def alerter_from_settings(settings) -> Alerter:
    return Alerter(
        AlertConfig(
            slack_webhook_url=settings.alert_slack_webhook,
            smtp_host=settings.smtp_host,
            smtp_port=settings.smtp_port,
            smtp_user=settings.smtp_user,
            smtp_password=settings.smtp_password,
            email_to=settings.alert_email_to,
        )
    )
