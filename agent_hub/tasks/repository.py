"""Async SQLite repository for tasks, events, gates, and handoffs.

Each method opens its own connection (cheap on SQLite). Foreign keys
are enforced; transitions are validated via state_machine.
"""

from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path

import aiosqlite

from agent_hub.state_machine import TaskStatus
from agent_hub.tasks.models import Task


def _utcnow_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _parse_dt(s: str) -> datetime:
    return datetime.fromisoformat(s)


def _row_to_task(row: aiosqlite.Row) -> Task:
    return Task(
        id=row[0],
        parent_id=row[1],
        title=row[2],
        description=row[3],
        status=TaskStatus(row[4]),
        owner=row[5],
        worktree_path=row[6],
        branch_name=row[7],
        origin_chat_id=row[8],
        created_at=_parse_dt(row[9]),
        updated_at=_parse_dt(row[10]),
    )


_TASK_COLS = (
    "id, parent_id, title, description, status, owner, "
    "worktree_path, branch_name, origin_chat_id, created_at, updated_at"
)


class TaskRepository:
    def __init__(self, db_path: Path):
        self.db_path = db_path

    def _connect(self) -> aiosqlite.Connection:
        """Return an aiosqlite Connection context-manager (not yet awaited).

        Usage::

            async with self._connect() as conn:
                ...

        Note: do NOT ``await self._connect()`` — aiosqlite.connect() returns
        a Connection that is itself an async context manager.  Awaiting it
        starts the background thread; entering it via ``async with`` also
        starts it, so double-starting raises RuntimeError on Python 3.14+.
        """
        return aiosqlite.connect(self.db_path)

    async def create(
        self,
        *,
        title: str,
        description: str,
        origin_chat_id: int,
        parent_id: int | None = None,
        owner: str | None = None,
    ) -> Task:
        now = _utcnow_iso()
        async with self._connect() as conn:
            await conn.execute("PRAGMA foreign_keys = ON")
            cur = await conn.execute(
                "INSERT INTO tasks "
                "(parent_id, title, description, status, owner, origin_chat_id, created_at, updated_at) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                (parent_id, title, description, TaskStatus.PENDING.value,
                 owner, origin_chat_id, now, now),
            )
            await conn.commit()
            task_id = cur.lastrowid
        return await self.get(task_id)  # type: ignore[return-value]

    async def get(self, task_id: int) -> Task | None:
        async with self._connect() as conn:
            await conn.execute("PRAGMA foreign_keys = ON")
            cur = await conn.execute(
                f"SELECT {_TASK_COLS} FROM tasks WHERE id = ?", (task_id,),
            )
            row = await cur.fetchone()
        return _row_to_task(row) if row else None
