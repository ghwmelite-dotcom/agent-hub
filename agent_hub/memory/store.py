"""Persistent project-scoped memory.

Keyed by workspace path; shared across all agents working on that workspace.
Auto-captured at orchestrator hook points (see memory/capture.py) and
injected into agent system prompts at task start (see agents/runner_options.py).
"""

from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import aiosqlite


_VALID_TYPES = {"project_fact", "lesson", "preference", "decision"}


def _utcnow_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


class MemoryStore:
    """Read/write the project_memory table.

    Async, per-call connect (matches the rest of the codebase).
    """

    def __init__(self, db_path: Path):
        self.db_path = db_path

    def _connect(self) -> Any:
        return aiosqlite.connect(self.db_path)

    async def insert(
        self,
        *,
        workspace: str,
        type: str,
        agent_source: str | None,
        title: str,
        body: str,
        related_task: int | None = None,
    ) -> int:
        """Insert a memory row, deduping on (workspace, type, title).

        On exact-title match (non-archived), no new row is inserted —
        instead `use_count` is bumped on the existing row and its id
        is returned. Body of the existing row is preserved.
        """
        if type not in _VALID_TYPES:
            raise ValueError(f"invalid memory type: {type!r}")
        async with self._connect() as conn:
            await conn.execute("PRAGMA foreign_keys = ON")
            conn.row_factory = aiosqlite.Row
            cur = await conn.execute(
                "SELECT id FROM project_memory "
                "WHERE workspace = ? AND type = ? AND title = ? AND archived = 0",
                (workspace, type, title),
            )
            existing = await cur.fetchone()
            if existing is not None:
                await conn.execute(
                    "UPDATE project_memory SET use_count = use_count + 1 "
                    "WHERE id = ?",
                    (existing["id"],),
                )
                await conn.commit()
                return int(existing["id"])

            cur = await conn.execute(
                "INSERT INTO project_memory "
                "(workspace, type, agent_source, title, body, related_task, created_at) "
                "VALUES (?, ?, ?, ?, ?, ?, ?)",
                (workspace, type, agent_source, title, body, related_task, _utcnow_iso()),
            )
            await conn.commit()
            return int(cur.lastrowid)

    async def list(
        self,
        *,
        workspace: str,
        type: str | None = None,
        include_archived: bool = False,
    ) -> list[dict]:
        """List entries for a workspace, newest first."""
        clauses = ["workspace = ?"]
        params: list[Any] = [workspace]
        if type is not None:
            clauses.append("type = ?")
            params.append(type)
        if not include_archived:
            clauses.append("archived = 0")
        where = " AND ".join(clauses)
        async with self._connect() as conn:
            conn.row_factory = aiosqlite.Row
            cur = await conn.execute(
                f"SELECT * FROM project_memory WHERE {where} "
                f"ORDER BY id DESC",
                params,
            )
            rows = await cur.fetchall()
        return [dict(r) for r in rows]

    async def archive(self, entry_id: int) -> None:
        """Soft delete — sets archived = 1."""
        async with self._connect() as conn:
            await conn.execute(
                "UPDATE project_memory SET archived = 1 WHERE id = ?",
                (entry_id,),
            )
            await conn.commit()
