"""Dead-man's switch — an INDEPENDENT process that flattens the account if the bot's
heartbeat goes stale during market hours.

Run it as a SEPARATE service from the bot (different process, ideally different
container/unit) so it can act when the bot itself has crashed or hung. The bot's
in-process kill switch protects against losses while it's alive; this protects
against the bot being dead with open positions.

    python -m scripts.watchdog --max-age 180 --interval 30
"""

from __future__ import annotations

import argparse
import logging
import time

from bot.alerting import alerter_from_settings
from bot.config import load_settings
from bot.execution.broker import Broker
from bot.heartbeat import heartbeat_age_seconds, read_heartbeat
from bot.logging_setup import setup_logging


def check_once(broker, alerter, heartbeat_path: str, max_age: float, log, *, already_fired: bool) -> bool:
    """One watchdog check. Returns the new 'already_fired' latch state.

    Flattens (once per staleness episode) when the market is open and the heartbeat is
    missing or older than max_age. The latch resets when a fresh heartbeat reappears, and
    is NOT set unless the account is confirmed flat — so a failed flatten retries next tick.
    """
    try:
        market_open = broker.is_market_open()
    except Exception:
        # Don't fail open: a broker outage is exactly when the bot may be dead. We can't
        # flatten without the API either, but surface the degradation instead of silence.
        log.exception("watchdog: cannot read market state")
        alerter.notify("critical", "watchdog DEGRADED — cannot reach broker", "market-state check failed")
        return already_fired

    if not market_open:
        return already_fired  # don't act outside market hours

    hb = read_heartbeat(heartbeat_path)
    age = heartbeat_age_seconds(hb) if hb else None
    # Stale if missing, unusable, too old, OR implausibly in the future (clock skew).
    stale = hb is None or age is None or age > max_age or age < -max_age

    if not stale:
        if already_fired:
            log.info("heartbeat recovered (age=%.0fs) — watchdog re-armed", age)
        return False  # fresh: reset the latch

    if already_fired:
        return True  # already handled this episode; don't spam

    log.critical("HEARTBEAT STALE (age=%s, max=%.0fs) — FLATTENING account", age, max_age)
    alerter.notify("critical", "watchdog: bot heartbeat stale — flattening", f"age={age}s")
    try:
        broker.flatten_all()
        remaining = broker.positions()
    except Exception:
        log.exception("watchdog flatten failed")
        alerter.notify("critical", "watchdog: FLATTEN FAILED — manual intervention needed", "")
        return False  # do NOT latch — retry on the next tick

    if remaining:
        log.critical("watchdog flatten incomplete — positions remain: %s", list(remaining))
        alerter.notify("critical", "watchdog: flatten incomplete — positions remain", str(list(remaining)))
        return False  # retry next tick until confirmed flat

    return True  # confirmed flat — latch the episode


def main() -> None:
    ap = argparse.ArgumentParser(description="Dead-man's-switch watchdog for the trading bot")
    ap.add_argument("--max-age", type=float, default=180.0, help="max heartbeat age (s) before flattening")
    ap.add_argument("--interval", type=float, default=30.0, help="check interval (s)")
    args = ap.parse_args()

    settings = load_settings()
    log = setup_logging(log_file="logs/watchdog.log")

    # Guard against a too-small max-age that would false-fire on a healthy bot. The bot
    # refreshes the heartbeat every safety poll (default 60s); require comfortable margin.
    if args.max_age < 120:
        log.warning(
            "--max-age %.0fs is low; must exceed the bot's heartbeat cadence "
            "(safety poll ~60s) by a safe margin or a healthy bot can be flattened.",
            args.max_age,
        )

    broker = Broker(settings)
    alerter = alerter_from_settings(settings)
    log.info("Watchdog started: max-age=%.0fs interval=%.0fs path=%s",
             args.max_age, args.interval, settings.heartbeat_path)

    fired = False
    while True:
        try:
            fired = check_once(broker, alerter, settings.heartbeat_path, args.max_age, log, already_fired=fired)
        except Exception:
            log.exception("watchdog loop error")
        time.sleep(args.interval)


if __name__ == "__main__":
    main()
