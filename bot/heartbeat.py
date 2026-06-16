"""Liveness heartbeat shared between the bot and the watchdog.

The bot writes a small JSON file at the end of each cycle; the independent watchdog
reads it and flattens the account if it goes stale during market hours. A plain file
(atomic-replace write) decouples the watchdog from the bot's SQLite ledger entirely.
"""

from __future__ import annotations

import json
import os
import threading
import uuid
from datetime import datetime, timezone
from pathlib import Path


def write_heartbeat(path: str, payload: dict) -> None:
    """Atomically write the heartbeat (timestamp stamped here) to ``path``.

    The temp file is unique per writer (pid+thread+uuid) so the bot's step and
    safety-poll threads can't share/clobber one another's temp; fsync + os.replace
    guarantee the live file is always one complete snapshot (last writer wins)."""
    data = {**payload, "ts": datetime.now(timezone.utc).isoformat()}
    p = Path(path)
    p.parent.mkdir(parents=True, exist_ok=True)
    tmp = p.with_name(f"{p.name}.{os.getpid()}.{threading.get_ident()}.{uuid.uuid4().hex}.tmp")
    try:
        with open(tmp, "w", encoding="utf-8") as f:
            f.write(json.dumps(data))
            f.flush()
            os.fsync(f.fileno())
        os.replace(tmp, p)  # atomic on POSIX and Windows
    finally:
        try:
            tmp.unlink()  # no-op after a successful replace; cleans up on failure
        except OSError:
            pass


def read_heartbeat(path: str) -> dict | None:
    """Return the heartbeat dict, or None if missing/unreadable/corrupt."""
    try:
        return json.loads(Path(path).read_text())
    except (OSError, ValueError):
        return None


def heartbeat_age_seconds(heartbeat: dict, now: datetime | None = None) -> float | None:
    """Seconds since the heartbeat was written, or None if its timestamp is unusable.
    A None result is treated as stale by the watchdog (fail-safe)."""
    try:
        ts = datetime.fromisoformat(heartbeat["ts"])
        if ts.tzinfo is None:  # tolerate a naive timestamp rather than raising on subtraction
            ts = ts.replace(tzinfo=timezone.utc)
        now = now or datetime.now(timezone.utc)
        return (now - ts).total_seconds()
    except (KeyError, TypeError, ValueError):
        return None
