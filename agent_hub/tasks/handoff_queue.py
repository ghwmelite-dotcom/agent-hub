"""Handoff queue — agent-to-agent dispatch messages.

Producers (agents via MCP) call enqueue().
Consumers (the orchestrator handoff loop, landing in a later plan)
call claim() atomically to pop a row.
"""

from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import aiosqlite

from agent_hub.tasks.models import HandoffRow


def _utcnow_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _parse_dt(s: str) -> datetime:
    return datetime.fromisoformat(s)


_COLS = "id, task_id, from_agent, to_agent, message, enqueued_at, claimed_at"


def _row_to_model(row) -> HandoffRow:
    return HandoffRow(
        id=row["id"],
        task_id=row["task_id"],
        from_agent=row["from_agent"],
        to_agent=row["to_agent"],
        message=row["message"],
        enqueued_at=_parse_dt(row["enqueued_at"]),
        claimed_at=_parse_dt(row["claimed_at"]) if row["claimed_at"] else None,
    )


class HandoffQueue:
    def __init__(self, db_path: Path):
        self.db_path = db_path

    def _connect(self) -> Any:
        return aiosqlite.connect(self.db_path)

    async def enqueue(
        self, *, task_id: int, from_agent: str, to_agent: str, message: str,
    ) -> int:
        async with self._connect() as conn:
            await conn.execute("PRAGMA foreign_keys = ON")
            conn.row_factory = aiosqlite.Row
            cur = await conn.execute(
                "INSERT INTO handoff_queue "
                "(task_id, from_agent, to_agent, message, enqueued_at) "
                "VALUES (?, ?, ?, ?, ?)",
                (task_id, from_agent, to_agent, message, _utcnow_iso()),
            )
            await conn.commit()
            return cur.lastrowid  # type: ignore[return-value]

    async def pending(self) -> list[HandoffRow]:
        async with self._connect() as conn:
            await conn.execute("PRAGMA foreign_keys = ON")
            conn.row_factory = aiosqlite.Row
            cur = await conn.execute(
                f"SELECT {_COLS} FROM handoff_queue WHERE claimed_at IS NULL "
                "ORDER BY enqueued_at ASC"
            )
            rows = await cur.fetchall()
        return [_row_to_model(r) for r in rows]

    async def claim(self) -> HandoffRow | None:
        """Atomically claim the oldest unclaimed row, or None if queue empty.

        Wrapped in BEGIN IMMEDIATE so two callers race on a write lock
        rather than both reading the same row.
        """
        now = _utcnow_iso()
        async with self._connect() as conn:
            await conn.execute("PRAGMA foreign_keys = ON")
            conn.row_factory = aiosqlite.Row
            await conn.execute("BEGIN IMMEDIATE")
            try:
                cur = await conn.execute(
                    "SELECT id FROM handoff_queue "
                    "WHERE claimed_at IS NULL "
                    "ORDER BY enqueued_at ASC LIMIT 1"
                )
                row = await cur.fetchone()
                if row is None:
                    await conn.execute("ROLLBACK")
                    return None
                row_id = row["id"]
                await conn.execute(
                    "UPDATE handoff_queue SET claimed_at = ? "
                    "WHERE id = ? AND claimed_at IS NULL",
                    (now, row_id),
                )
                cur = await conn.execute(
                    f"SELECT {_COLS} FROM handoff_queue WHERE id = ?", (row_id,),
                )
                fetched = await cur.fetchone()
                await conn.commit()
            except Exception:
                await conn.execute("ROLLBACK")
                raise
        return _row_to_model(fetched) if fetched else None
