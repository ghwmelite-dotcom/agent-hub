"""Async SQLite repository for tasks, events, gates, and handoffs.

Each method opens its own connection (cheap on SQLite). Foreign keys
are enforced; transitions are validated via state_machine.
"""

from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import aiosqlite

from agent_hub.state_machine import TaskStatus
from agent_hub.tasks.models import Task


def _utcnow_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _parse_dt(s: str) -> datetime:
    return datetime.fromisoformat(s)


def _row_to_task(row: aiosqlite.Row) -> Task:
    return Task(
        id=row["id"],
        parent_id=row["parent_id"],
        title=row["title"],
        description=row["description"],
        status=TaskStatus(row["status"]),
        owner=row["owner"],
        worktree_path=row["worktree_path"],
        branch_name=row["branch_name"],
        origin_chat_id=row["origin_chat_id"],
        created_at=_parse_dt(row["created_at"]),
        updated_at=_parse_dt(row["updated_at"]),
    )


_TASK_COLS = (
    "id, parent_id, title, description, status, owner, "
    "worktree_path, branch_name, origin_chat_id, created_at, updated_at"
)


class TaskRepository:
    def __init__(self, db_path: Path):
        self.db_path = db_path

    def _connect(self) -> Any:
        """Open a fresh connection with FK enforcement + named-row access.

        NOTE: do NOT `await self._connect()`. aiosqlite.connect() is both
        awaitable and an async context manager. On Python 3.14, awaiting
        then entering raises RuntimeError ('threads can only be started
        once'). Always use `async with self._connect() as conn:` with no
        await, then PRAGMA + row_factory inside the block.
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
            conn.row_factory = aiosqlite.Row
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
            conn.row_factory = aiosqlite.Row
            cur = await conn.execute(
                f"SELECT {_TASK_COLS} FROM tasks WHERE id = ?", (task_id,),
            )
            row = await cur.fetchone()
        return _row_to_task(row) if row else None

    async def list(
        self,
        *,
        status: TaskStatus | None = None,
        owner: str | None = None,
        parent_id: int | None = None,
    ) -> list[Task]:
        clauses: list[str] = []
        params: list = []
        if status is not None:
            clauses.append("status = ?")
            params.append(status.value)
        if owner is not None:
            clauses.append("owner = ?")
            params.append(owner)
        if parent_id is not None:
            clauses.append("parent_id = ?")
            params.append(parent_id)
        where = ("WHERE " + " AND ".join(clauses)) if clauses else ""
        sql = f"SELECT {_TASK_COLS} FROM tasks {where} ORDER BY id ASC"
        async with self._connect() as conn:
            await conn.execute("PRAGMA foreign_keys = ON")
            conn.row_factory = aiosqlite.Row
            cur = await conn.execute(sql, tuple(params))
            rows = await cur.fetchall()
        return [_row_to_task(r) for r in rows]

    async def tree(self, root_id: int) -> dict | None:
        """Returns {'root': Task, 'descendants': list[Task]} or None if root_id is unknown."""
        root = await self.get(root_id)
        if root is None:
            return None
        descendants: list[Task] = []
        frontier = [root.id]
        async with self._connect() as conn:
            await conn.execute("PRAGMA foreign_keys = ON")
            conn.row_factory = aiosqlite.Row
            while frontier:
                placeholders = ",".join("?" * len(frontier))
                cur = await conn.execute(
                    f"SELECT {_TASK_COLS} FROM tasks WHERE parent_id IN ({placeholders})",
                    tuple(frontier),
                )
                rows = await cur.fetchall()
                children = [_row_to_task(r) for r in rows]
                descendants.extend(children)
                frontier = [c.id for c in children]
        return {"root": root, "descendants": descendants}
