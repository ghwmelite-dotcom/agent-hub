"""Human-in-the-loop gates (design approval).

In v1 the only `kind` is "design". When the architect calls
gate.request(...), the task pauses on `design_review` status; the
orchestrator (later plan) detects the pending row and DMs the user.
"""

from __future__ import annotations

from datetime import datetime, timezone
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
