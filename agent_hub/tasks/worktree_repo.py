"""SQLite repository for the worktrees table.

One row per task. The WorktreeManager (agent_hub/worktree_manager.py)
uses this for state; the orchestrator (Plan 3) reads it during
restart-resume and orphan detection.
"""

from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import aiosqlite

from agent_hub.tasks.models import Worktree


def _utcnow_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _parse_dt(s: str) -> datetime:
    return datetime.fromisoformat(s)


_COLS = "task_id, path, branch, base_branch, created_at, cleaned_at"


def _row_to_worktree(row) -> Worktree:
    return Worktree(
        task_id=row["task_id"],
        path=row["path"],
        branch=row["branch"],
        base_branch=row["base_branch"],
        created_at=_parse_dt(row["created_at"]),
        cleaned_at=_parse_dt(row["cleaned_at"]) if row["cleaned_at"] else None,
    )


class WorktreeRepository:
    def __init__(self, db_path: Path):
        self.db_path = db_path

    def _connect(self) -> Any:
        return aiosqlite.connect(self.db_path)

    async def record(
        self, *, task_id: int, path: str, branch: str, base_branch: str,
    ) -> None:
        async with self._connect() as conn:
            await conn.execute("PRAGMA foreign_keys = ON")
            conn.row_factory = aiosqlite.Row
            await conn.execute(
                "INSERT INTO worktrees (task_id, path, branch, base_branch, created_at) "
                "VALUES (?, ?, ?, ?, ?)",
                (task_id, path, branch, base_branch, _utcnow_iso()),
            )
            await conn.commit()

    async def get_by_task(self, task_id: int) -> Worktree | None:
        async with self._connect() as conn:
            await conn.execute("PRAGMA foreign_keys = ON")
            conn.row_factory = aiosqlite.Row
            cur = await conn.execute(
                f"SELECT {_COLS} FROM worktrees WHERE task_id = ?", (task_id,),
            )
            row = await cur.fetchone()
        return _row_to_worktree(row) if row else None
