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


def _await_flat(broker, log, timeout: float, poll: float) -> dict:
    """Poll positions() until flat or timeout. flatten_all() only SUBMITS async closes — the
    fills land seconds later — so reading positions() immediately would falsely report
    'positions remain'. Returns the remaining positions (empty dict == confirmed flat); a
    sentinel non-empty dict if the read fails (treated as not-yet-flat, so the caller retries)."""
    import time as _time

    deadline = _time.monotonic() + timeout
    while True:
        try:
            remaining = broker.positions()
        except Exception:
            log.exception("watchdog: positions() read failed during flatten confirm")
            return {"_unread": 0.0}  # can't confirm flat -> don't latch
        if not remaining or _time.monotonic() >= deadline:
            return remaining
        _time.sleep(poll)


def check_once(broker, alerter, heartbeat_path: str, max_age: float, log, *, already_fired: bool,
               confirm_timeout: float = 15.0, confirm_poll: float = 1.0,
               startup_grace: float = 0.0, elapsed: float | None = None,
               is_crypto: bool = False) -> bool:
    """One watchdog check. Returns the new 'already_fired' latch state.

    Flattens (once per staleness episode) when the market is open and the heartbeat is
    missing or older than max_age. The latch resets when a fresh heartbeat reappears, and
    is NOT set unless the account is confirmed flat (after a bounded poll for the async close
    fills) — so a failed/incomplete flatten retries next tick.

    Crypto trades 24/7, so ``is_crypto`` makes the market always 'open' — otherwise the
    dead-man's-switch would be disabled nights/weekends (the stock clock), exactly when a
    crashed crypto bot would carry exposure through 24/7 markets.

    Startup grace: a MISSING heartbeat within ``startup_grace`` seconds of launch (``elapsed``)
    is treated as not-yet-stale — the bot may simply not have written its first heartbeat yet,
    and flattening then would liquidate a healthy account at cold start. An EXISTING-but-old
    heartbeat is always immediately stale (a genuine crash), regardless of grace.
    """
    if is_crypto:
        market_open = True  # 24/7 — the dead-man's switch must be armed at all times
    else:
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
    in_grace = elapsed is not None and elapsed <= startup_grace
    if hb is None:
        # Within the cold-start grace, a missing heartbeat is the bot still starting up, not a
        # dead bot — do NOT flatten yet. After the grace, a missing heartbeat is genuinely stale.
        if in_grace:
            return already_fired
        stale = True
    else:
        # data/heartbeat.json persists between runs, so at cold start the file we read may be a
        # PRIOR-SESSION leftover, not this bot. A heartbeat OLDER than the watchdog's own uptime
        # cannot belong to the bot we just started (it writes within the grace), so within the
        # grace treat it as not-yet-stale. After the grace, a still-old heartbeat is a real crash.
        if in_grace and age is not None and age > elapsed:
            return already_fired
        # Existing heartbeat: stale if unusable, too old, OR implausibly in the future (skew).
        stale = age is None or age > max_age or age < -max_age

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
    except Exception:
        log.exception("watchdog flatten failed")
        alerter.notify("critical", "watchdog: FLATTEN FAILED — manual intervention needed", "")
        return False  # do NOT latch — retry on the next tick

    # Confirm flat with a bounded poll (close fills are async) before judging completeness.
    remaining = _await_flat(broker, log, confirm_timeout, confirm_poll)
    if remaining:
        log.critical("watchdog flatten incomplete — positions remain: %s", list(remaining))
        alerter.notify("critical", "watchdog: flatten incomplete — positions remain", str(list(remaining)))
        return False  # retry next tick until confirmed flat

    return True  # confirmed flat — latch the episode


def main() -> None:
    ap = argparse.ArgumentParser(description="Dead-man's-switch watchdog for the trading bot")
    ap.add_argument("--max-age", type=float, default=180.0, help="max heartbeat age (s) before flattening")
    ap.add_argument("--interval", type=float, default=30.0, help="check interval (s)")
    ap.add_argument("--startup-grace", type=float, default=None,
                    help="seconds to tolerate a MISSING heartbeat at launch before flattening "
                         "(default: max(max_age, 120) — lets the bot write its first heartbeat)")
    args = ap.parse_args()
    startup_grace = args.startup_grace if args.startup_grace is not None else max(args.max_age, 120.0)

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
    is_crypto = settings.market == "crypto"
    start = time.monotonic()
    log.info("Watchdog started: max-age=%.0fs interval=%.0fs startup-grace=%.0fs market=%s path=%s",
             args.max_age, args.interval, startup_grace, settings.market, settings.heartbeat_path)

    fired = False
    overnight_alerted = False
    while True:
        try:
            fired = check_once(broker, alerter, settings.heartbeat_path, args.max_age, log,
                               already_fired=fired, startup_grace=startup_grace,
                               elapsed=time.monotonic() - start, is_crypto=is_crypto)
            # A bot that died with positions open into/after the close can't be flattened by a
            # market-closed watchdog (DAY closes won't fill until the next open). Surface it once
            # so a human can act, instead of silently carrying the position overnight. Crypto is
            # 24/7 — never "closed" — so this stock-only path is skipped (check_once flattens).
            if not is_crypto and not broker.is_market_open():
                hb = read_heartbeat(settings.heartbeat_path)
                age = heartbeat_age_seconds(hb) if hb else None
                stale = hb is None or age is None or age > args.max_age or age < -args.max_age
                if stale and broker.positions():
                    if not overnight_alerted:
                        log.critical("positions held with market CLOSED and heartbeat stale — manual action needed")
                        alerter.notify("critical", "watchdog: positions held overnight, bot stale",
                                       "DAY closes cannot flatten until the next open — intervene manually")
                        overnight_alerted = True
                else:
                    overnight_alerted = False
            else:
                overnight_alerted = False
        except Exception:
            log.exception("watchdog loop error")
        time.sleep(args.interval)


if __name__ == "__main__":
    main()
