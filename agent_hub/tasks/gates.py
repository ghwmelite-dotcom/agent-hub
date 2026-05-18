"""Human-in-the-loop gates (design approval).

In v1 the only `kind` is "design". When the architect calls
gate.request(...), the task pauses on `design_review` status; the
orchestrator (later plan) detects the pending row and DMs the user.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

import aiosqlite

from agent_hub.tasks.models import Gate


def _utcnow_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _parse_dt(s: str) -> datetime:
    return datetime.fromisoformat(s)


_COLS = "id, task_id, kind, artifact_path, summary, requested_at, resolved_at, resolution"


def _row_to_gate(row) -> Gate:
    return Gate(
        id=row["id"],
        task_id=row["task_id"],
        kind=row["kind"],
        artifact_path=row["artifact_path"],
        summary=row["summary"],
        requested_at=_parse_dt(row["requested_at"]),
        resolved_at=_parse_dt(row["resolved_at"]) if row["resolved_at"] else None,
        resolution=row["resolution"],
    )


class GateRepository:
    def __init__(self, db_path: Path):
        self.db_path = db_path

    def _connect(self) -> Any:
        return aiosqlite.connect(self.db_path)

    async def request(
        self, *, task_id: int, kind: str,
        artifact_path: str | None = None, summary: str | None = None,
    ) -> int:
        async with self._connect() as conn:
            await conn.execute("PRAGMA foreign_keys = ON")
            conn.row_factory = aiosqlite.Row
            cur = await conn.execute(
                "INSERT INTO gates (task_id, kind, artifact_path, summary, requested_at) "
                "VALUES (?, ?, ?, ?, ?)",
                (task_id, kind, artifact_path, summary, _utcnow_iso()),
            )
            await conn.commit()
            return cur.lastrowid  # type: ignore[return-value]

    async def get(self, gate_id: int) -> Gate | None:
        async with self._connect() as conn:
            await conn.execute("PRAGMA foreign_keys = ON")
            conn.row_factory = aiosqlite.Row
            cur = await conn.execute(
                f"SELECT {_COLS} FROM gates WHERE id = ?", (gate_id,),
            )
            row = await cur.fetchone()
        return _row_to_gate(row) if row else None

    async def unresolved_unnotified(self) -> list[Gate]:
        """Gates awaiting user action that we haven't DM'd about yet.

        Used by the gate watcher: announce each gate at most once across
        the orchestrator's lifetime, including across restarts. The
        in-memory `_notified_gates` set used to be a runtime-only filter;
        it's now persisted via the `notified_at` column.
        """
        async with self._connect() as conn:
            await conn.execute("PRAGMA foreign_keys = ON")
            conn.row_factory = aiosqlite.Row
            cur = await conn.execute(
                f"SELECT {_COLS}, notified_at FROM gates "
                "WHERE resolved_at IS NULL AND notified_at IS NULL "
                "ORDER BY requested_at ASC"
            )
            rows = await cur.fetchall()
        return [_row_to_gate(r) for r in rows]

    async def mark_notified(self, gate_id: int) -> None:
        """Record that we DM'd the user about this gate. Idempotent."""
        async with self._connect() as conn:
            await conn.execute(
                "UPDATE gates SET notified_at = ? "
                "WHERE id = ? AND notified_at IS NULL",
                (_utcnow_iso(), gate_id),
            )
            await conn.commit()

    async def needing_reminder(
        self,
        *,
        now: datetime | None = None,
        timeout_hours: float = 24.0,
    ) -> list[Gate]:
        """Gates that have been pending too long and need a nudge DM.

        Returns gates where:
        - resolved_at IS NULL (still pending)
        - notified_at IS NOT NULL (already announced once — the
          gate-watcher's first pass handled them on creation)
        - requested_at is older than `timeout_hours` ago
        - last_reminder_at is either NULL or older than `timeout_hours`
          ago (so reminders fire at most every `timeout_hours`)
        """
        cutoff = (now or datetime.now(timezone.utc)) - timedelta(hours=timeout_hours)
        cutoff_iso = cutoff.isoformat()
        async with self._connect() as conn:
            await conn.execute("PRAGMA foreign_keys = ON")
            conn.row_factory = aiosqlite.Row
            cur = await conn.execute(
                f"SELECT {_COLS}, notified_at, last_reminder_at FROM gates "
                "WHERE resolved_at IS NULL "
                "AND notified_at IS NOT NULL "
                "AND requested_at < ? "
                "AND (last_reminder_at IS NULL OR last_reminder_at < ?) "
                "ORDER BY requested_at ASC",
                (cutoff_iso, cutoff_iso),
            )
            rows = await cur.fetchall()
        return [_row_to_gate(r) for r in rows]

    async def mark_reminder_sent(self, gate_id: int) -> None:
        """Update last_reminder_at to now. Always rewrites — explicit."""
        async with self._connect() as conn:
            await conn.execute(
                "UPDATE gates SET last_reminder_at = ? WHERE id = ?",
                (_utcnow_iso(), gate_id),
            )
            await conn.commit()

    async def count_unresolved(self) -> int:
        """Count of gates still awaiting user resolution — for /status."""
        async with self._connect() as conn:
            cur = await conn.execute(
                "SELECT COUNT(*) FROM gates WHERE resolved_at IS NULL"
            )
            row = await cur.fetchone()
        return int(row[0]) if row else 0

    async def status(self, *, task_id: int, kind: str) -> str:
        """Returns 'pending' | 'approved' | 'rejected' | 'none'."""
        async with self._connect() as conn:
            await conn.execute("PRAGMA foreign_keys = ON")
            conn.row_factory = aiosqlite.Row
            cur = await conn.execute(
                "SELECT resolution, resolved_at FROM gates "
                "WHERE task_id = ? AND kind = ? "
                "ORDER BY requested_at DESC LIMIT 1",
                (task_id, kind),
            )
            row = await cur.fetchone()
        if row is None:
            return "none"
        if row["resolved_at"] is None:
            return "pending"
        return row["resolution"] or "pending"

    async def resolve(self, *, task_id: int, kind: str, resolution: str) -> None:
        """Resolve the latest pending gate for (task_id, kind). Idempotent."""
        async with self._connect() as conn:
            await conn.execute("PRAGMA foreign_keys = ON")
            conn.row_factory = aiosqlite.Row
            cur = await conn.execute(
                "SELECT id, resolved_at FROM gates "
                "WHERE task_id = ? AND kind = ? "
                "ORDER BY requested_at DESC LIMIT 1",
                (task_id, kind),
            )
            row = await cur.fetchone()
            if row is None:
                raise ValueError(f"No gate exists for task={task_id} kind={kind!r}")
            if row["resolved_at"] is not None:
                return  # Already resolved — no-op
            await conn.execute(
                "UPDATE gates SET resolved_at = ?, resolution = ? WHERE id = ?",
                (_utcnow_iso(), resolution, row["id"]),
            )
            await conn.commit()
