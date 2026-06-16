"""SQLite order ledger.

The broker is always the source of truth for positions; this ledger records what
the bot *intended* and what it *submitted* (keyed by client_order_id for
idempotency) so it can reconcile after a crash or restart and avoid double-orders.

Thread-safe: the live loop runs ``step`` on an APScheduler worker thread, so the
connection is opened with check_same_thread=False and every access is serialized
with a lock.
"""

from __future__ import annotations

import sqlite3
import threading
from contextlib import closing
from datetime import datetime, timezone
from pathlib import Path

_SCHEMA = """
CREATE TABLE IF NOT EXISTS orders (
    client_order_id TEXT PRIMARY KEY,
    broker_order_id TEXT,
    symbol          TEXT NOT NULL,
    side            TEXT NOT NULL,
    qty             REAL NOT NULL,
    status          TEXT NOT NULL,
    reason          TEXT,
    created_at      TEXT NOT NULL
);
"""


class Ledger:
    def __init__(self, path: str = "data/ledger.sqlite") -> None:
        Path(path).parent.mkdir(parents=True, exist_ok=True)
        self._lock = threading.Lock()
        self.conn = sqlite3.connect(path, check_same_thread=False)
        self.conn.row_factory = sqlite3.Row
        with self._lock, closing(self.conn.cursor()) as cur:
            cur.executescript(_SCHEMA)
            self.conn.commit()

    def record_intent(
        self, client_order_id: str, symbol: str, side: str, qty: float, reason: str = ""
    ) -> bool:
        """Record intent. Returns True if newly inserted, False if it already existed."""
        with self._lock, closing(self.conn.cursor()) as cur:
            cur.execute(
                "INSERT OR IGNORE INTO orders "
                "(client_order_id, symbol, side, qty, status, reason, created_at) "
                "VALUES (?, ?, ?, ?, 'intended', ?, ?)",
                (client_order_id, symbol, side, qty, reason, _now()),
            )
            self.conn.commit()
            return cur.rowcount == 1

    def mark_submitted(self, client_order_id: str, broker_order_id: str) -> None:
        self._update(client_order_id, status="submitted", broker_order_id=broker_order_id)

    def mark_status(self, client_order_id: str, status: str) -> None:
        self._update(client_order_id, status=status)

    def already_submitted(self, client_order_id: str) -> bool:
        with self._lock, closing(self.conn.cursor()) as cur:
            cur.execute(
                "SELECT status FROM orders WHERE client_order_id = ?", (client_order_id,)
            )
            row = cur.fetchone()
        return bool(row) and row["status"] in ("submitted", "filled")

    def reconcile(self, broker_positions: dict[str, float]) -> dict[str, float]:
        """Return the broker's live positions (the truth). Callers should adopt this
        and log any drift; extend to repair local state as the bot grows."""
        return dict(broker_positions)

    def _update(self, client_order_id: str, **fields: object) -> None:
        cols = ", ".join(f"{k} = ?" for k in fields)
        with self._lock, closing(self.conn.cursor()) as cur:
            cur.execute(
                f"UPDATE orders SET {cols} WHERE client_order_id = ?",
                (*fields.values(), client_order_id),
            )
            self.conn.commit()

    def close(self) -> None:
        with self._lock:
            self.conn.close()


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()
