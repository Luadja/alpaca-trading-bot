"""Mechanical go-live gate — a measurable bar a strategy must clear before real money.

Reads local state (config, ledger, persisted kill-switch, heartbeat) and prints PASS/FAIL
per gate. Exits non-zero if any REQUIRED gate fails, so it can guard a deploy script.
It does NOT make anything live — it only reports readiness.

    python -m scripts.go_live_check --min-trades 20
"""

from __future__ import annotations

import argparse
import json
import sys

from bot.alerting import alerter_from_settings
from bot.config import load_settings
from bot.heartbeat import heartbeat_age_seconds, read_heartbeat
from bot.state import Ledger

VALIDATED_STRATEGIES = {"trend_momentum"}  # mean-reversion (stoch_rsi_mfi) was retired


def evaluate_gates(
    *,
    strategy: str,
    filled_orders: int,
    min_trades: int,
    halted: bool,
    heartbeat_age: float | None,
    alerting_enabled: bool,
) -> list[tuple[str, bool, bool, str]]:
    """Pure gate evaluation. Returns (name, passed, required, detail) per gate."""
    return [
        ("strategy is validated", strategy in VALIDATED_STRATEGIES, True, strategy),
        ("paper track record", filled_orders >= min_trades, True,
         f"{filled_orders}/{min_trades} filled paper orders"),
        ("kill switch not latched", not halted, True, "HALTED" if halted else "ok"),
        ("bot has actually run", heartbeat_age is not None, True,
         "no heartbeat found" if heartbeat_age is None else f"heartbeat {heartbeat_age:.0f}s old"),
        ("alerting configured", alerting_enabled, False,
         "configured" if alerting_enabled else "log-only - set ALERT_SLACK_WEBHOOK or SMTP_*"),
    ]


def _halted_from_state(ledger: Ledger) -> bool:
    raw = ledger.get_state("risk")
    if not raw:
        return False
    try:
        s = json.loads(raw)
        return bool(s.get("halted_day") or s.get("halted_week") or s.get("halted_month") or s.get("halted"))
    except (ValueError, TypeError):
        return False


def main() -> int:
    ap = argparse.ArgumentParser(description="Go-live readiness gate")
    ap.add_argument("--min-trades", type=int, default=20, help="min filled paper orders required")
    args = ap.parse_args()

    settings = load_settings()
    ledger = Ledger(settings.ledger_path)
    counts = ledger.status_counts()
    filled = counts.get("filled", 0) + counts.get("partially_filled", 0)
    halted = _halted_from_state(ledger)
    ledger.close()

    hb = read_heartbeat(settings.heartbeat_path)
    hb_age = heartbeat_age_seconds(hb) if hb else None

    gates = evaluate_gates(
        strategy=settings.strategy,
        filled_orders=filled,
        min_trades=args.min_trades,
        halted=halted,
        heartbeat_age=hb_age,
        alerting_enabled=alerter_from_settings(settings).enabled,
    )

    print(f"Go-live readiness (paper={settings.paper}, strategy={settings.strategy}):\n")
    required_failed = 0
    for name, passed, required, detail in gates:
        mark = "PASS" if passed else ("FAIL" if required else "WARN")
        print(f"  [{mark}] {name}: {detail}")
        if required and not passed:
            required_failed += 1

    if required_failed:
        print(f"\nNOT READY - {required_failed} required gate(s) failed.")
        return 1
    print("\nREADY - all required gates passed. (Still ramp capital slowly.)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
